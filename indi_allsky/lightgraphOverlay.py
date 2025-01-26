import math
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import time
import ephem
import numpy
import cv2
from PIL import Image
from PIL import ImageFont
from PIL import ImageDraw
import logging


logger = logging.getLogger('indi_allsky')


class IndiAllSkyLightgraphOverlay(object):

    top_border = 0
    text_area_height = 50


    def __init__(self, config, position_av):
        self.config = config
        self.position_av = position_av

        self.lightgraph = None
        self.next_generate = 0  # generate immediately


        self.graph_height = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('GRAPH_HEIGHT', 30)
        self.graph_border = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('GRAPH_BORDER', 3)
        self.now_marker_size = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('NOW_MARKER_SIZE', 8)

        self.offset_y = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('OFFSET_Y', -10)
        self.offset_x = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('OFFSET_X', 0)

        self.opacity = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('OPACITY', 100)

        self.label = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('LABEL', False)
        self.hour_lines = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('HOUR_LINES', True)

        base_path  = Path(__file__).parent
        self.font_path  = base_path.joinpath('fonts')


    def apply(self, image_data):
        now = time.time()

        if now > self.next_generate:
            self.lightgraph = self.generate()


        lightgraph = self.lightgraph.copy()


        graph_height, graph_width = lightgraph.shape[:2]


        now = datetime.now()
        noon = datetime.strptime(now.strftime('%Y%m%d12'), '%Y%m%d%H')

        now_offset = int((now - noon).seconds / 60) + self.graph_border


        # draw now triangle
        now_tri = numpy.array([
            (now_offset - self.now_marker_size, (self.top_border + self.graph_height + self.graph_border) - self.now_marker_size),
            (now_offset + self.now_marker_size, (self.top_border + self.graph_height + self.graph_border) - self.now_marker_size),
            (now_offset, self.top_border + self.graph_height + self.graph_border),
        ],
            dtype=numpy.int32,
        )
        #logger.info(now_tri)


        now_color_bgr = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('NOW_COLOR', (15, 150, 200)))
        now_color_bgr.reverse()

        cv2.fillPoly(
            img=lightgraph,
            pts=[now_tri],
            color=tuple(now_color_bgr),
        )


        # create alpha channel, anything pixel that is full black (0, 0, 0) is transparent
        alpha = numpy.max(lightgraph, axis=2)
        alpha[alpha > 0] = int(255 * (self.opacity / 100))
        lightgraph = numpy.dstack((lightgraph, alpha))


        # separate layers
        lightgraph_bgr = lightgraph[:, :, :3]
        lightgraph_alpha = (lightgraph[:, :, 3] / 255).astype(numpy.float32)

        # create alpha mask
        alpha_mask = numpy.dstack((
            lightgraph_alpha,
            lightgraph_alpha,
            lightgraph_alpha,
        ))


        lightgraph_height, lightgraph_width = lightgraph_bgr.shape[:2]
        image_height, image_width = image_data.shape[:2]


        crop_y1 = 0 - self.offset_y  # y is usually negative
        crop_y2 = lightgraph_height - self.offset_y
        crop_x1 = int((image_width / 2) - (lightgraph_width / 2) + self.offset_x)
        crop_x2 = int((image_width / 2) + (lightgraph_width / 2) + self.offset_x)


        # extract are where lightgraph is to be applied
        image_crop = image_data[
            crop_y1:crop_y2,
            crop_x1:crop_x2,
        ]


        # apply alpha mask
        image_crop = (image_crop * (1 - alpha_mask) + lightgraph_bgr * alpha_mask).astype(numpy.uint8)


        if self.label:
            # Keogram labels enabled by default
            image_label_system = self.config.get('IMAGE_LABEL_SYSTEM', 'pillow')

            if image_label_system == 'opencv':
                image_crop = self.drawText_opencv(image_crop)
            else:
                # pillow is default
                image_crop = self.drawText_pillow(image_crop)
        else:
            logger.warning('Lightgraph labels disabled')


        # add overlayed lightgraph area back to image
        image_data[
            crop_y1:crop_y2,
            crop_x1:crop_x2,
        ] = image_crop


    def generate(self):
        generate_start = time.time()


        now = datetime.now()
        utc_offset = now.astimezone().utcoffset()

        noon = datetime.strptime(now.strftime('%Y%m%d12'), '%Y%m%d%H')
        self.next_generate = (noon + timedelta(hours=24)).timestamp()

        noon_utc = noon - utc_offset


        obs = ephem.Observer()
        obs.lat = math.radians(self.position_av[0])
        obs.lon = math.radians(self.position_av[1])

        # disable atmospheric refraction calcs
        obs.pressure = 0

        sun = ephem.Sun()


        day_color_bgr = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('DAY_COLOR', (150, 150, 150)))
        day_color_bgr.reverse()
        night_color_bgr = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('NIGHT_COLOR', (30, 30, 30)))
        night_color_bgr.reverse()


        lightgraph_list = list()
        for x in range(1440):
            obs.date = noon_utc + timedelta(minutes=x)
            sun.compute(obs)

            sun_alt_deg = math.degrees(sun.alt)

            if sun_alt_deg < -18:
                lightgraph_list.append(night_color_bgr)
            elif sun_alt_deg > 0:
                lightgraph_list.append(day_color_bgr)
            else:
                norm = (18 + sun_alt_deg) / 18  # alt is negative
                lightgraph_list.append(self.mapColor(norm, day_color_bgr, night_color_bgr))

        #logger.info(lightgraph_list)

        generate_elapsed_s = time.time() - generate_start
        logger.warning('Total lightgraph processing in %0.4f s', generate_elapsed_s)


        lightgraph = numpy.array([lightgraph_list], dtype=numpy.uint8)
        lightgraph = cv2.resize(
            lightgraph,
            (1440, self.graph_height),
            interpolation=cv2.INTER_AREA,
        )


        if self.hour_lines:
            # draw hour ticks
            lineType = getattr(cv2, self.config['TEXT_PROPERTIES']['FONT_AA'])

            hour_color_bgr = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('HOUR_COLOR', (15, 15, 100)))
            hour_color_bgr.reverse()

            for x in range(1, 24):
                cv2.line(
                    img=lightgraph,
                    pt1=(60 * x, 0),
                    pt2=(60 * x, self.graph_height),
                    color=tuple(hour_color_bgr),
                    thickness=1,
                    lineType=lineType,
                )


        # draw border
        border_color_bgr = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('BORDER_COLOR', (1, 1, 1)))
        border_color_bgr.reverse()

        lightgraph = cv2.copyMakeBorder(
            lightgraph,
            self.graph_border,
            self.graph_border,
            self.graph_border,
            self.graph_border,
            cv2.BORDER_CONSTANT,
            None,
            tuple(border_color_bgr),
        )


        # draw text area
        lightgraph = cv2.copyMakeBorder(
            lightgraph,
            self.top_border,
            self.text_area_height,
            0,
            0,
            cv2.BORDER_CONSTANT,
            None,
            (0, 0, 0)
        )


        return lightgraph


    def drawText_opencv(self, lightgraph):
        fontFace = getattr(cv2, self.config['TEXT_PROPERTIES']['FONT_FACE'])
        lineType = getattr(cv2, self.config['TEXT_PROPERTIES']['FONT_AA'])

        font_color_bgr = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('FONT_COLOR', (150, 150, 150)))
        font_color_bgr.reverse()

        for x, hour in enumerate([13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]):
            cv2.putText(
                img=lightgraph,
                text=str(hour),
                org=((60 * (x + 1)) + self.graph_border - 7, self.top_border + self.graph_height + (self.graph_border * 2) + 20),
                fontFace=fontFace,
                color=(1, 1, 1),  # not full black
                lineType=lineType,
                fontScale=self.config['LIGHTGRAPH_OVERLAY']['OPENCV_FONT_SCALE'],
                thickness=self.config['TEXT_PROPERTIES']['FONT_THICKNESS'] + 1,
            )
            cv2.putText(
                img=lightgraph,
                text=str(hour),
                org=((60 * (x + 1)) + self.graph_border - 7, self.top_border + self.graph_height + (self.graph_border * 2) + 20),
                fontFace=fontFace,
                color=tuple(font_color_bgr),
                lineType=lineType,
                fontScale=self.config['LIGHTGRAPH_OVERLAY']['OPENCV_FONT_SCALE'],
                thickness=self.config['TEXT_PROPERTIES']['FONT_THICKNESS'] + 1,
            )


        return lightgraph


    def drawText_pillow(self, lightgraph):
        lightgraph_rgb = Image.fromarray(cv2.cvtColor(lightgraph, cv2.COLOR_BGR2RGB))
        width, height  = lightgraph_rgb.size  # backwards from opencv


        if self.config['TEXT_PROPERTIES']['PIL_FONT_FILE'] == 'custom':
            pillow_font_file_p = Path(self.config['TEXT_PROPERTIES']['PIL_FONT_CUSTOM'])
        else:
            pillow_font_file_p = self.font_path.joinpath(self.config['TEXT_PROPERTIES']['PIL_FONT_FILE'])


        pillow_font_size = self.config.get('LIGHTGRAPH_OVERLAY', {}).get('PIL_FONT_SIZE', 20)

        font = ImageFont.truetype(str(pillow_font_file_p), pillow_font_size)
        draw = ImageDraw.Draw(lightgraph_rgb)

        color_rgb = list(self.config.get('LIGHTGRAPH_OVERLAY', {}).get('FONT_COLOR', (200, 200, 200)))  # RGB for pillow


        if self.config['TEXT_PROPERTIES']['FONT_OUTLINE']:
            # black outline
            stroke_width = 2
        else:
            stroke_width = 0


        for x, hour in enumerate([13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]):
            draw.text(
                ((60 * (x + 1)) + self.graph_border, self.top_border + self.graph_height + (self.graph_border * 2) + 1),
                str(hour),
                fill=tuple(color_rgb),
                font=font,
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0),
                anchor='ma',  # middle-ascender
            )


        # convert back to numpy array
        return cv2.cvtColor(numpy.array(lightgraph_rgb), cv2.COLOR_RGB2BGR)


    def mapColor(self, scale, color_high, color_low):
        return tuple(int(((x[0] - x[1]) * scale) + x[1]) for x in zip(color_high, color_low))
