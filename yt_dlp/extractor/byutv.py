import re

from .common import InfoExtractor
from ..utils import determine_ext, js_to_json, merge_dicts, parse_iso8601, url_or_none
from ..utils.traversal import traverse_obj


class BYUtvIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?byutv\.org/(?P<id>[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})(?:/(?P<display_id>[^/?#&]+))?'
    _TESTS = [
        {
            'url': 'https://www.byutv.org/8c99eccc-25f0-470b-a021-ea43e3190808/seven-ate-nine',
            'info_dict': {
                'id': '8c99eccc-25f0-470b-a021-ea43e3190808',
                'display_id': 'seven-ate-nine',
                'ext': 'mp4',
                'title': 'Seven Ate Nine',
                'description': 'md5:fe27ed07939af90a715008aceba12aa9',
                'thumbnail': r're:^https?://.*\.webp$',
                'duration': 1285.12,
                'episode': 'Seven Ate Nine',
                'episode_number': 3,
                'season_number': 22,
                'series': 'Studio C',
                'timestamp': 1776664800,
                'upload_date': '20260420',
            },
            'params': {
                'skip_download': True,
            },
        },
        {
            # URL without slug
            'url': 'https://www.byutv.org/69bf895c-fccf-41f9-a9d9-83cc2db2d6f0',
            'only_matching': True,
        },
        {
            # Movie
            'url': 'https://www.byutv.org/063453eb-87bc-41d9-8489-9165ef7a8e4c/apollo-13',
            'info_dict': {
                'id': '063453eb-87bc-41d9-8489-9165ef7a8e4c',
                'display_id': 'apollo-13',
                'ext': 'mp4',
                'title': 'Apollo 13',
                'description': 'md5:9703ca981bb2c6b1f5856fe78f18fd39',
                'thumbnail': r're:^https?://.*\.webp$',
                'duration': 8071.168,
                'timestamp': 788943600,
                'upload_date': '19950101',
            },
            'params': {
                'skip_download': True,
            },
        },
    ]

    def _real_extract(self, url):
        mobj = self._match_valid_url(url)
        video_id = mobj.group('id')
        display_id = mobj.group('display_id') or video_id

        formats = []
        subtitles = {}

        video_page = self._download_webpage(url, video_id)
        byutv_config = self._search_json(
            r'window\.__NUXT__\.config\s*=',
            video_page,
            'site config',
            video_id,
            contains_pattern=r'\{(?s:.+?)\}',
            end_pattern=r'</script>',
            transform_source=js_to_json,
        )

        request_headers = {
            'x-byub-client': traverse_obj(byutv_config, ('public', 'apiKey', {str})),
            'x-byub-location': 'us',
            'x-byub-session': '',
            'x-byub-device': '',
            'x-byub-clientversion': traverse_obj(
                byutv_config,
                ('public', 'version', {str}),
            ),
        }
        views_data = self._download_json(
            f'https://api.byub.org/views/v1/public/pages/{video_id}',
            video_id,
            'Downloading target info from views endpoint',
            'Failed to dwnload target info from views endpoint',
            headers=request_headers,
        )

        target_info = (
            traverse_obj(
                views_data,
                (
                    'sections',
                    lambda _, v: v['type'] == 'play-details-section',
                    'content',
                    any,
                ),
            )
            or {}
        )

        media_id = traverse_obj(target_info, ('media', 'id', {str}))
        media_info = self._download_json(
            f'https://api.byub.org/media/v1/public/media/{media_id}',
            video_id,
            'Downloading media information',
            'Failed to download media information',
            headers=request_headers,
        )

        info = {
            'id': video_id,
            'disply_id': display_id,
            'title': traverse_obj(target_info, ('title', {str})),
            'description': traverse_obj(target_info, ('shortDescription', {str}))
            or traverse_obj(target_info, ('longDescription', {str})),
            'thumbnails': traverse_obj(
                target_info,
                (
                    'images',
                    lambda _, v: v['maxSize'] and v['maxSize'].split('x').length > 1,
                    {
                        lambda v: {
                            'id': v['type'],
                            'url': f'{v["baseUrl"]}/{v["imageId"]}/{v["maxSize"]}.webp',
                            'width': v['maxSize'].split('x')[0],
                            'height': v['maxSize'].split('x')[1],
                        },
                    },
                    all,
                ),
            ),
            'duration': self._parse_duration(traverse_obj(media_info, 'length', {str})),
            'timestamp': traverse_obj(target_info, ('airDate', {parse_iso8601})),
            'age_limit': traverse_obj(target_info, ('rating', {str}, any)),
            'categories': traverse_obj(target_info, ('category', 'name', {str})),
        }

        if traverse_obj(target_info, ('type', {str})) == 'episode':
            info = merge_dicts(
                info,
                {
                    'episode': traverse_obj(target_info, ('title', {str})),
                    'episode_number': traverse_obj(
                        target_info,
                        ('episodeNumber', {str}),
                    ),
                    'season_number': traverse_obj(
                        target_info,
                        (
                            'parents',
                            lambda _, v: v['type'] == 'season',
                            'seasonNumber',
                            {int},
                            any,
                        ),
                    ),
                    'season': traverse_obj(
                        target_info,
                        (
                            'parents',
                            lambda _, v: v['type'] == 'season',
                            'title',
                            {str},
                            any,
                        ),
                    ),
                    'series': traverse_obj(
                        target_info,
                        (
                            'parents',
                            lambda _, v: v['type'] == 'show',
                            'title',
                            {str},
                            any,
                        ),
                    ),
                    'series_id': traverse_obj(
                        target_info,
                        ('parents', lambda _, v: v['type'] == 'show', 'id', {str}, any),
                    ),
                },
            )

        for asset_url in traverse_obj(
            media_info,
            ('assets', ..., 'url', {url_or_none}),
        ):
            ext = determine_ext(asset_url)

            match ext:
                case 'm3u8':
                    m3u8_fmts, m3u8_subs = self._extract_m3u8_formats_and_subtitles(
                        asset_url,
                        video_id,
                        'mp4',
                        entry_protocol='m3u8_native',
                        m3u8_id='hls',
                        fatal=False,
                    )
                    formats.extend(m3u8_fmts)
                    subtitles = self._merge_subtitles(subtitles, m3u8_subs)
                case 'mpd':
                    mpd_fmts, mpd_subs = self._extract_mpd_formats_and_subtitles(
                        asset_url,
                        video_id,
                        mpd_id='dash',
                        fatal=False,
                    )
                    formats.extend(mpd_fmts)
                    subtitles = self._merge_subtitles(subtitles, mpd_subs)
                case _:
                    formats.append(
                        {
                            'url': asset_url,
                            'format_id': video_id,
                        },
                    )

        return merge_dicts(
            info,
            {
                'formats': formats,
                'subtitles': subtitles,
            },
        )

    @staticmethod
    def _parse_duration(duration_str):
        # Duration is a .NET TimeSpan string, e.g. '0.00:22:05.0560000'
        # (days.hours:minutes:seconds.fraction, days/fraction optional)
        mobj = re.fullmatch(
            r'(?:(?P<days>\d+)\.)?(?P<hours>\d+):(?P<mins>\d+):(?P<secs>\d+(?:\.\d+)?)',
            duration_str or '',
        )
        if not mobj:
            return None
        days, hours, mins, secs = mobj.group('days', 'hours', 'mins', 'secs')
        return int(days or 0) * 86400 + int(hours) * 3600 + int(mins) * 60 + float(secs)
