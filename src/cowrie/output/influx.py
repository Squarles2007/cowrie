import re

from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError

from twisted.python import log

import cowrie.core.output
from cowrie.core.config import CowrieConfig

import requests
import json
import geohash


class Output(cowrie.core.output.Output):
    """
    influx output
    """
    def start(self):
        host = CowrieConfig().get('output_influx', 'host', fallback='')
        port = CowrieConfig().getint('output_influx', 'port', fallback=8086)
        ssl = CowrieConfig().getboolean('output_influx', 'ssl', fallback=False)

        self.client = None

        self.ipstack_api_key = None

        try:
            self.client = InfluxDBClient(host=host, port=port, ssl=ssl, verify_ssl=ssl)
        except InfluxDBClientError as e:
            log.err("output_influx: I/O error({0}): '{1}'".format(
                e.errno, e.strerror))
            return

        if self.client is None:
            log.err("output_influx: cannot instantiate client!")
            return

        if (CowrieConfig().has_option('output_influx', 'username') and
                CowrieConfig().has_option('output_influx', 'password')):
            username = CowrieConfig().get('output_influx', 'username')
            password = CowrieConfig().get('output_influx', 'password', raw=True)
            self.client.switch_user(username, password)

        try:
            dbname = CowrieConfig().get('output_influx', 'database_name')
        except Exception:
            dbname = 'cowrie'

        retention_policy_duration_default = '12w'
        retention_policy_name = dbname + "_retention_policy"

        if CowrieConfig().has_option('output_influx', 'retention_policy_duration'):
            retention_policy_duration = CowrieConfig().get(
                'output_influx', 'retention_policy_duration')

            match = re.search(r'^\d+[dhmw]{1}$', retention_policy_duration)
            if not match:
                log.err(("output_influx: invalid retention policy."
                         "Using default '{}'..").format(
                    retention_policy_duration))
                retention_policy_duration = retention_policy_duration_default
        else:
            retention_policy_duration = retention_policy_duration_default

        database_list = self.client.get_list_database()
        dblist = [str(elem['name']) for elem in database_list]

        if dbname not in dblist:
            self.client.create_database(dbname)
            self.client.create_retention_policy(
                retention_policy_name, retention_policy_duration, 1,
                database=dbname, default=True)
        else:
            retention_policies_list = self.client.get_list_retention_policies(
                database=dbname)
            rplist = [str(elem['name']) for elem in retention_policies_list]
            if retention_policy_name not in rplist:
                self.client.create_retention_policy(
                    retention_policy_name, retention_policy_duration, 1,
                    database=dbname, default=True)
            else:
                self.client.alter_retention_policy(
                    retention_policy_name, database=dbname,
                    duration=retention_policy_duration,
                    replication=1, default=True)

        self.client.switch_database(dbname)

        if CowrieConfig().has_option("ipstack_api", "ipstack_api_key"):
            self.ipstack_api_key = CowrieConfig().get("ipstack_api", "ipstack_api_key")

    def stop(self):
        pass

    def write(self, entry):
        if self.client is None:
            log.err("output_influx: client object is not instantiated")
            return

        # event id
        eventid = entry['eventid']

        # measurement init
        m = {
            'measurement': eventid.replace('.', '_'),
            'tags': {
                'session': entry['session'],
                'src_ip': entry['src_ip']
            },
            'fields': {
                'sensor': self.sensor
            },
        }

        # event parsing
        if eventid in ['cowrie.command.failed',
                       'cowrie.command.input']:
            m['fields'].update({
                'input': entry['input'],
            })

        elif eventid == 'cowrie.session.connect':
            if self.ipstack_api_key != None:
                url = "http://api.ipstack.com/" + entry['src_ip'] + "?access_key=" + self.ipstack_api_key + "&format=1"
                try:
                    response = requests.get(url)
                    response = json.loads(response.text)
                    m['fields'].update({
                        'protocol': entry['protocol'],
                        'src_ip': entry['src_ip'],
                        'src_port': entry['src_port'],
                        'dst_port': entry['dst_port'],
                        'dst_ip': entry['dst_ip'],
                        'lat': response['latitude'],
                        'long': response['longitude'],
                        'geohash': geohash.encode(response['latitude'], response['longitude']),
                        'city': response['city'],
                        'region_code': response['region_code'],
                        'country_code': response['country_code'],
                        'continent_code': response['continent_code'],
                    })
                    m['tags'].update({
                        'geohash': geohash.encode(response['latitude'], response['longitude']),
                        'city': response['city'],
                        'region_code': response['region_code'],
                        'country_code': response['country_code'],
                        'continent_code': response['continent_code'],
                    })
                except requests.exceptions.RequestException as e:
                    log.err("output_influx: I/O error({0}): '{1}'".format(
                        e.errno, e.strerror))
                    m['fields'].update({
                        'protocol': entry['protocol'],
                        'src_ip': entry['src_ip'],
                        'src_port': entry['src_port'],
                        'dst_port': entry['dst_port'],
                        'dst_ip': entry['dst_ip'],

                    })



            else:
                m['fields'].update({
                    'protocol': entry['protocol'],
                    'src_ip': entry['src_ip'],
                    'src_port': entry['src_port'],
                    'dst_port': entry['dst_port'],
                    'dst_ip': entry['dst_ip'],

                })

        elif eventid in ['cowrie.login.success', 'cowrie.login.failed']:
            m['fields'].update({
                'username': entry['username'],
                'password': entry['password'],
            })

        elif eventid == 'cowrie.session.file_download':
            m['fields'].update({
                'shasum': entry.get('shasum'),
                'url': entry.get('url'),
                'outfile': entry.get('outfile')
            })

        elif eventid == 'cowrie.session.file_download.failed':
            m['fields'].update({
                'url': entry.get('url')
            })

        elif eventid == 'cowrie.session.file_upload':
            m['fields'].update({
                'shasum': entry.get('shasum'),
                'outfile': entry.get('outfile'),
            })

        elif eventid == 'cowrie.session.closed':
            m['fields'].update({
                'duration': entry['duration']
            })

        elif eventid == 'cowrie.client.version':
            m['fields'].update({
                'version': ','.join(entry['version']),
            })

        elif eventid == 'cowrie.client.kex':
            m['fields'].update({
                'maccs': ','.join(entry['macCS']),
                'kexalgs': ','.join(entry['kexAlgs']),
                'keyalgs': ','.join(entry['keyAlgs']),
                'compcs': ','.join(entry['compCS']),
                'enccs': ','.join(entry['encCS'])
            })

        elif eventid == 'cowrie.client.size':
            m['fields'].update({
                'height': entry['height'],
                'width': entry['width'],
            })

        elif eventid == 'cowrie.client.var':
            m['fields'].update({
                'name': entry['name'],
                'value': entry['value'],
            })

        elif eventid == 'cowrie.client.fingerprint':
            m['fields'].update({
                'fingerprint': entry['fingerprint']
            })

            # cowrie.direct-tcpip.data, cowrie.direct-tcpip.request
            # cowrie.log.closed
            # are not implemented
        else:
            # other events should be handled
            log.err(
                "output_influx: event '{}' not handled. Skipping..".format(
                    eventid))
            return

        result = self.client.write_points([m])

        if not result:
            log.err("output_influx: error when writing '{}' measurement"
                    "in the db.".format(eventid))
