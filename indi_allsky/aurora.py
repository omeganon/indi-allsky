import time
import socket
import ssl
import math
import json
import urllib3.exceptions
import requests
import numpy
import logging

from .flask import db
from .flask.miscDb import miscDb


logger = logging.getLogger('indi_allsky')


class IndiAllskyAuroraUpdate(object):

    ovation_json_url = 'https://services.swpc.noaa.gov/json/ovation_aurora_latest.json'
    kpindex_json_url = 'https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json'


    def __init__(self, config):
        self.config = config

        self._miscDb = miscDb(self.config)

        self.ovation_json_data = None
        self.kpindex_json_data = None


    def update(self, camera):
        # allow data to be reused
        if not self.ovation_json_data:
            try:
                self.ovation_json_data = self.download_json(self.ovation_json_url)
            except json.JSONDecodeError as e:
                logger.error('JSON parse error: %s', str(e))
                self.ovation_json_data = None
            except socket.gaierror as e:
                logger.error('Name resolution error: %s', str(e))
                self.ovation_json_data = None
            except socket.timeout as e:
                logger.error('Timeout error: %s', str(e))
                self.ovation_json_data = None
            except requests.exceptions.ConnectTimeout as e:
                logger.error('Connection timeout: %s', str(e))
                self.ovation_json_data = None
            except requests.exceptions.ConnectionError as e:
                logger.error('Connection error: %s', str(e))
                self.ovation_json_data = None
            except requests.exceptions.ReadTimeout as e:
                logger.error('Connection error: %s', str(e))
                self.ovation_json_data = None
            except urllib3.exceptions.ReadTimeoutError as e:
                logger.error('Connection error: %s', str(e))
                self.ovation_json_data = None
            except ssl.SSLCertVerificationError as e:
                logger.error('Certificate error: %s', str(e))
                self.ovation_json_data = None
            except requests.exceptions.SSLError as e:
                logger.error('Certificate error: %s', str(e))
                self.ovation_json_data = None


        # allow data to be reused
        if not self.kpindex_json_data:
            try:
                self.kpindex_json_data = self.download_json(self.kpindex_json_url)
            except json.JSONDecodeError as e:
                logger.error('JSON parse error: %s', str(e))
                self.kpindex_json_data = None
            except socket.gaierror as e:
                logger.error('Name resolution error: %s', str(e))
                self.kpindex_json_data = None
            except socket.timeout as e:
                logger.error('Timeout error: %s', str(e))
                self.kpindex_json_data = None
            except requests.exceptions.ConnectTimeout as e:
                logger.error('Connection timeout: %s', str(e))
                self.kpindex_json_data = None
            except requests.exceptions.ConnectionError as e:
                logger.error('Connection error: %s', str(e))
                self.kpindex_json_data = None
            except urllib3.exceptions.ReadTimeoutError as e:
                logger.error('Connection error: %s', str(e))
                self.kpindex_json_data = None
            except ssl.SSLCertVerificationError as e:
                logger.error('Certificate error: %s', str(e))
                self.kpindex_json_data = None
            except requests.exceptions.SSLError as e:
                logger.error('Certificate error: %s', str(e))
                self.kpindex_json_data = None


        latitude = camera.latitude
        longitude = camera.longitude


        if camera.data:
            camera_data = dict(camera.data)
        else:
            camera_data = dict()


        update_camera = False

        if self.ovation_json_data:
            max_ovation, avg_ovation = self.processOvationLocationData(self.ovation_json_data, latitude, longitude)
            logger.info('Max Ovation: %d', max_ovation)
            logger.info('Avg Ovation: %0.2f', avg_ovation)

            camera_data['OVATION_MAX'] = max_ovation
            update_camera = True


        if self.kpindex_json_data:
            kpindex, kpindex_poly = self.processKpindexPoly(self.kpindex_json_data)
            logger.info('kpindex: %0.2f', kpindex)
            logger.info('Data: x = %0.2f, b = %0.2f', kpindex_poly.coef[0], kpindex_poly.coef[1])

            camera_data['KPINDEX_CURRENT'] = round(kpindex, 2)
            camera_data['KPINDEX_COEF'] = round(kpindex_poly.coef[0], 2)
            update_camera = True


        if update_camera:
            camera_data['AURORA_DATA_TS'] = int(time.time())
            camera.data = camera_data
            db.session.commit()


    def download_json(self, url):
        logger.warning('Downloading %s', url)

        r = requests.get(url, allow_redirects=True, verify=True, timeout=(15.0, 30.0))

        if r.status_code >= 400:
            logger.error('URL returned %d', r.status_code)
            return None

        json_data = json.loads(r.text)
        #logger.warning('Response: %s', json_data)

        return json_data


    def processOvationLocationData(self, json_data, latitude, longitude):
        # this will check a 5 degree by 5 degree grid and aggregate all of the ovation scores

        logger.warning('Looking up data for %0.1f, %0.1f', latitude, longitude)

        if longitude < 0:
            longitude = 360 + longitude  # logitude is negative


        # 1 degree is ~69 miles, 7 degrees should be just under 500 miles

        lat_floor = math.floor(latitude)
        # this will not work exactly right at the north and south poles above 80 degrees latitude
        lat_list = [
            lat_floor - 7,
            lat_floor - 6,
            lat_floor - 5,
            lat_floor - 4,
            lat_floor - 3,
            lat_floor - 2,
            lat_floor - 1,
            lat_floor,
            lat_floor + 1,
            lat_floor + 2,
            lat_floor + 3,
            lat_floor + 4,
            lat_floor + 5,
            lat_floor + 6,
            lat_floor + 7,
        ]

        long_floor = math.floor(longitude)
        long_list = [
            long_floor - 7,  # this should cover northern and southern hemispheres
            long_floor - 6,
            long_floor - 5,
            long_floor - 4,
            long_floor - 3,
            long_floor - 2,
            long_floor - 1,
            long_floor,
            long_floor + 1,
            long_floor + 2,
            long_floor + 3,
            long_floor + 4,
            long_floor + 5,
            long_floor + 6,
            long_floor + 7,
        ]


        # fix longitudes that cross 0/360
        for i in long_list:
            if i < 0:
                i = 360 + i  # i is negative
            elif i > 360:
                i = i - 360


        data_list = list()
        for i in json_data['coordinates']:
            #logger.info('%s', i)

            for long_val in long_list:
                for lat_val in lat_list:
                    if i[0] == long_val and i[1] == lat_val:
                        data_list.append(int(i[2]))


        #logger.info('Data: %s', data_list)

        return max(data_list), sum(data_list) / len(data_list)


    def processKpindexPoly(self, json_data):
        kp_last = float(json_data[-1][1])

        json_iter = iter(json_data)
        next(json_iter)  # skip first index

        kp_list = list()
        for k in json_iter:
            try:
                kp_list.append(float(k[1]))
            except ValueError:
                logger.error('Invalid float: %s', str(k[1]))
                continue


        #logger.info('kpindex data: %s', kp_list)

        x = numpy.arange(0, len(kp_list))
        y = numpy.array(kp_list)

        # use linear regression to calculate general trend
        p_fitted = numpy.polynomial.Polynomial.fit(x, y, deg=1)

        return kp_last, p_fitted.convert()


