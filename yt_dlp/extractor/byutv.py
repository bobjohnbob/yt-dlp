import re
import uuid

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    determine_ext,
    int_or_none,
    parse_age_limit,
    parse_iso8601,
    url_or_none,
)
from ..utils.traversal import traverse_obj


def _parse_timespan(value):
    """Parse a .NET TimeSpan (``[d.]hh:mm:ss[.fffffff]``) into seconds"""
    mobj = re.fullmatch(
        r'(?:(?P<days>\d+)\.)?(?P<hours>\d+):(?P<mins>\d{1,2}):(?P<secs>\d{1,2}(?:\.\d+)?)',
        str(value or '').strip())
    if not mobj:
        return None
    return (
        int(mobj.group('days') or 0) * 86400
        + int(mobj.group('hours')) * 3600
        + int(mobj.group('mins')) * 60
        + float(mobj.group('secs')))


class BYUtvIE(InfoExtractor):
    _VALID_URL = r'''(?x)
        https?://(?:www\.)?byutv\.org/
        (?:(?:watch|player)/(?!event/))?
        (?P<id>[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12})
        (?:/(?P<display_id>[^/?#&]+))?'''
    _TESTS = [{
        'url': 'https://www.byutv.org/6587b9a3-89d2-42a6-a7f7-fd2f81840a7d/season-5-episode-5',
        'info_dict': {
            'id': '6587b9a3-89d2-42a6-a7f7-fd2f81840a7d',
            'display_id': 'season-5-episode-5',
            'ext': 'mp4',
            'title': 'Season 5 Episode 5',
            'description': 'Matt gives his last words, and Hunger Games characters try to decide who Katniss should marry.',
            'thumbnail': r're:https?://.+',
            'duration': 1486.592,
            'series': 'Studio C',
            'season': 'Season 5',
            'season_number': 5,
            'episode': 'Episode 5',
            'episode_number': 5,
            'upload_date': '20141103',
            'timestamp': 1414998000,
            'age_limit': 0,
        },
        'params': {'skip_download': True},
    }, {
        # legacy /watch/ URL, still redirects on the site
        'url': 'http://www.byutv.org/watch/6587b9a3-89d2-42a6-a7f7-fd2f81840a7d/studio-c-season-5-episode-5',
        'only_matching': True,
    }, {
        'url': 'https://www.byutv.org/player/8f1dab9b-b243-47c8-b525-3e2d021a3451/byu-softball-pacific-vs-byu-41219---game-2',
        'only_matching': True,
    }, {
        'url': 'https://www.byutv.org/c0221346-f76b-4372-9b3d-0ab94f7340f0',
        'only_matching': True,
    }]

    _API_BASE = 'https://api.byub.org'
    # Public web client key, from the site's Nuxt runtime config
    _CLIENT = 'byutv-web-dk94tsvophi'
    _CLIENT_VERSION = '5.69.0'

    def _call_api(self, path, video_id, note, expected_status=None):
        return self._download_json(
            f'{self._API_BASE}/{path}', video_id, note=note,
            expected_status=expected_status, headers={
                'x-byub-client': self._CLIENT,
                'x-byub-clientversion': self._CLIENT_VERSION,
                'x-byub-device': str(uuid.uuid4()),
                'x-byub-session': str(uuid.uuid4()),
                'x-byub-location': 'us',
                'x-byub-isauthenticated': 'false',
                'x-byub-offset': '0',
                'accept-language': 'en',
                'Origin': 'https://www.byutv.org',
                'Referer': 'https://www.byutv.org/',
            })

    def _real_extract(self, url):
        video_id, display_id = self._match_valid_url(url).group('id', 'display_id')

        item = self._call_api(
            f'views/v2/public/content/{video_id}', video_id, 'Downloading content JSON')
        content = item.get('content') or {}

        media_id = item.get('mediaId')
        if not media_id:
            raise ExtractorError('This content has no playable media', expected=True)
        if traverse_obj(item, ('media', 'requireLogin')):
            self.raise_login_required('This video requires a BYUtv account')

        media = self._call_api(
            f'media/v1/public/media/{media_id}', video_id, 'Downloading media JSON',
            expected_status=(401, 403, 404))
        if error := media.get('message'):
            if media.get('type') == 'Unauthorized' or 'login' in error.lower():
                self.raise_login_required(error)
            raise ExtractorError(error, expected=True)

        formats, subtitles = [], {}
        for asset in traverse_obj(media, ('assets', lambda _, v: url_or_none(v['url']))):
            asset_url = asset['url']
            asset_type = asset.get('assetType')
            if asset_type == 'dash1' or determine_ext(asset_url) == 'mpd':
                # DASH is Widevine-protected (rmt=wv), HLS below is not
                continue
            if asset_type == 'hls1' or determine_ext(asset_url) == 'm3u8':
                fmts, subs = self._extract_m3u8_formats_and_subtitles(
                    asset_url, video_id, 'mp4', m3u8_id='hls', fatal=False)
                formats.extend(fmts)
                self._merge_subtitles(subs, target=subtitles)

        if not formats:
            self.raise_no_formats(
                'No DRM-free formats found', video_id=video_id, expected=True)

        parents = traverse_obj(content, ('parents', ...))
        show = traverse_obj(parents, (lambda _, v: v['sourceType'] == 'show', any))
        season = traverse_obj(parents, (lambda _, v: v['sourceType'] == 'season', any))

        # The image service only serves sizes matching the image's declared
        # aspect ratio, so scale each one to a long edge of 1280
        thumbnails = []
        for image in traverse_obj(content, ('images', lambda _, v: v['imageId'] and v['baseUrl'])):
            ratio_w, _, ratio_h = (image.get('aspectRatio') or '16:9').partition(':')
            ratio_w, ratio_h = int_or_none(ratio_w), int_or_none(ratio_h)
            if not ratio_w or not ratio_h:
                continue
            width = 1280 if ratio_w >= ratio_h else round(1280 * ratio_w / ratio_h)
            height = 1280 if ratio_h >= ratio_w else round(1280 * ratio_h / ratio_w)
            thumbnails.append({
                'id': image.get('type'),
                'url': f'{image["baseUrl"]}/{image["imageId"]}/{width}x{height}.webp',
                'width': width,
                'height': height,
                'preference': 1 if image.get('type') == 'content' else -1,
            })

        return {
            'id': video_id,
            'display_id': display_id or video_id,
            'formats': formats,
            'subtitles': subtitles,
            'thumbnails': thumbnails,
            'title': content.get('title') or display_id,
            'description': content.get('longDescription') or content.get('shortDescription'),
            'duration': _parse_timespan(content.get('length')),
            'timestamp': parse_iso8601(content.get('airDate')),
            'series': traverse_obj(show, 'title'),
            'season': traverse_obj(season, 'title'),
            'season_number': int_or_none(traverse_obj(season, 'seasonNumber')),
            'episode_number': int_or_none(content.get('episodeNumber')),
            'age_limit': parse_age_limit(
                content.get('rating') or traverse_obj(parents, (..., 'rating', any))),
        }
