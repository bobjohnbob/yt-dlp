import re
import uuid

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    determine_ext,
    js_to_json,
    parse_iso8601,
    url_or_none,
)
from ..utils.traversal import traverse_obj


class BYUtvIE(InfoExtractor):
    _VALID_URL = (r'https?://(?:www\.)?byutv\.org/'
                  r'(?P<id>[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})(?:/(?P<display_id>[^/?#&]+))?')
    _TESTS = [{
        'url': 'https://www.byutv.org/69bf895c-fccf-41f9-a9d9-83cc2db2d6f0/pretty-pretty-dope-with-josh-peck',
        'info_dict': {
            'id': '69bf895c-fccf-41f9-a9d9-83cc2db2d6f0',
            'display_id': 'pretty-pretty-dope-with-josh-peck',
            'ext': 'mp4',
            'title': 'Pretty Pretty Dope with Josh Peck',
            'description': 'md5:26531b68b276029328ef8bcd6b9ab643',
            'thumbnail': r're:^https?://.*\.webp$',
            'duration': 1325.056,
            'episode': 'Pretty Pretty Dope with Josh Peck',
            'episode_number': 1,
            'season_number': 21,
            'series': 'Studio C',
            'timestamp': 1760335200,
            'upload_date': '20251013',
        },
        'params': {
            'skip_download': True,
        },
    }, {
        # URL without slug
        'url': 'https://www.byutv.org/69bf895c-fccf-41f9-a9d9-83cc2db2d6f0',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        video_id, display_id = self._match_valid_url(url).group('id', 'display_id')
        display_id = display_id or video_id

        # The site's client API key/version are baked into the page's Nuxt
        # runtime config on every load, so grab them fresh instead of
        # hardcoding values that go stale whenever BYUtv ships a new build.
        webpage = self._download_webpage(url, video_id)
        site_config = self._search_json(
            r'window\.__NUXT__\.config\s*=', webpage, 'site config', video_id,
            contains_pattern=r'\{(?s:.+?)\}', end_pattern=r'</script>',
            transform_source=js_to_json)

        api_headers = {
            'x-byub-client': traverse_obj(site_config, ('public', 'apiKey', {str})),
            'x-byub-location': 'us',
            'x-byub-session': str(uuid.uuid4()),
            'x-byub-device': str(uuid.uuid4()),
            'x-byub-clientversion': traverse_obj(site_config, ('public', 'version', {str})),
        }

        views_data = self._download_json(
            f'https://api.byub.org/views/v1/public/pages/{video_id}',
            video_id, 'Downloading views API JSON', headers=api_headers)
        content = traverse_obj(views_data, (
            'sections', lambda _, v: v['type'] == 'play-details-section',
            'content', any)) or {}

        media_id = traverse_obj(content, ('media', 'id', {str}))
        if not media_id:
            raise ExtractorError('Unable to find media ID for this video')

        media_data = self._download_json(
            f'https://api.byub.org/media/v1/public/media/{media_id}',
            video_id, 'Downloading media API JSON', headers=api_headers)

        formats, subtitles = [], {}
        for asset_url in traverse_obj(media_data, (
                'assets', ..., 'url', {url_or_none})):
            ext = determine_ext(asset_url)
            if ext == 'm3u8':
                fmts, subs = self._extract_m3u8_formats_and_subtitles(
                    asset_url, video_id, 'mp4', m3u8_id='hls', fatal=False)
            elif ext == 'mpd':
                fmts, subs = self._extract_mpd_formats_and_subtitles(
                    asset_url, video_id, mpd_id='dash', fatal=False)
            else:
                continue
            formats.extend(fmts)
            self._merge_subtitles(subs, target=subtitles)

        for transcript in traverse_obj(media_data, ('transcripts', lambda _, v: v['url'])):
            subtitles.setdefault(transcript.get('language') or 'en', []).append({
                'url': transcript['url'],
                'ext': 'json',
            })

        image = traverse_obj(content, (
            'images', lambda _, v: v['aspectRatio'] == '16:9' and v['type'] == 'content', any))

        return {
            'id': video_id,
            'display_id': display_id,
            'title': traverse_obj(content, ('title', {str})),
            'description': traverse_obj(
                content, (('shortDescription', 'longDescription'), {str}, any)),
            'thumbnail': f'{image["baseUrl"]}/{image["imageId"]}/1280x720.webp' if image else None,
            'duration': self._parse_byub_duration(traverse_obj(media_data, ('length', {str}))),
            'episode': traverse_obj(content, ('title', {str})),
            'episode_number': traverse_obj(content, ('episodeNumber', {int})),
            'season_number': traverse_obj(content, (
                'parents', lambda _, v: v['type'] == 'season', 'seasonNumber', {int}, any)),
            'series': traverse_obj(content, (
                'parents', lambda _, v: v['type'] == 'show', 'title', {str}, any)),
            'timestamp': traverse_obj(content, ('airDate', {parse_iso8601})),
            'formats': formats,
            'subtitles': subtitles,
        }

    @staticmethod
    def _parse_byub_duration(duration_str):
        # Duration is a .NET TimeSpan string, e.g. '0.00:22:05.0560000'
        # (days.hours:minutes:seconds.fraction, days/fraction optional)
        mobj = re.fullmatch(
            r'(?:(?P<days>\d+)\.)?(?P<hours>\d+):(?P<mins>\d+):(?P<secs>\d+(?:\.\d+)?)',
            duration_str or '')
        if not mobj:
            return None
        days, hours, mins, secs = mobj.group('days', 'hours', 'mins', 'secs')
        return int(days or 0) * 86400 + int(hours) * 3600 + int(mins) * 60 + float(secs)
