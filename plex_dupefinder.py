#!/usr/bin/env python3

# https://github.com/adilusprimus/plex_dupefinder

# Set Datadog environment variables early
import os
os.environ['DD_SERVICE'] = 'plex_dupefinder'
os.environ['DD_REQUESTS_SERVICE'] = 'plex'
os.environ['DD_ENV'] = 'production'
os.environ['DD_VERSION'] = '1.0.6'
os.environ['DD_TRACE_DEBUG'] = 'false'

import ddtrace.sourcecode.setuptools_auto
import sys
import logging
import collections
import concurrent.futures
import logging
import os
import sys
import time
from functools import lru_cache
from fnmatch import fnmatch
from tabulate import tabulate
from config import cfg
try:
    from urlparse import urljoin
except ImportError:
    from urllib.parse import urljoin
from plexapi.server import PlexServer
import requests
from ddtrace.debugging import DynamicInstrumentation
from ddtrace import tracer    
from openfeature.evaluation_context import EvaluationContext

# Start the Datadog tracer
tracer.configure()

# Create and register the Datadog OpenFeature provider
# DynamicInstrumentation.enable()  # Disabled due to probe installation conflicts
from ddtrace import patch
patch(logging=True)
patch(requests=True)

############################################################
# INIT
############################################################

# Setup logger
FORMAT = ('%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] '
          '[dd.service=%(dd.service)s dd.env=%(dd.env)s dd.version=%(dd.version)s dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] '
          '- %(message)s')


log_filename = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), 'activity.log')
logging.basicConfig(
    filename=log_filename,
    level=logging.DEBUG,
    format=FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.getLogger('urllib3.connectionpool').disabled = True

# Set JSON formatter for file handler
from pythonjsonlogger import jsonlogger
for handler in logging.getLogger().handlers:
    if isinstance(handler, logging.FileHandler):
        handler.setFormatter(jsonlogger.JsonFormatter(FORMAT))

# Add console handler for simultaneous console output
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logging.getLogger('').addHandler(console_handler)

log = logging.getLogger(__name__) 

# Optional OpenFeature support
try:
    from openfeature import api as openfeature_api
    from ddtrace.openfeature import DatadogProvider
    from openfeature.evaluation_context import EvaluationContext
except Exception:
    openfeature_api = None
    DatadogProvider = None
    EvaluationContext = None

if openfeature_api and DatadogProvider:
    provider = DatadogProvider()
    openfeature_api.set_provider(provider)

    client = openfeature_api.get_client("plex_dupefinder")

    eval_ctx = EvaluationContext(
        targeting_key="Test",
        attributes={"userId": "tester", "userRole": "tester"},
    )

    value = client.get_boolean_value(
        flag_key="feature-flag-test",
        default_value=False,
        evaluation_context=eval_ctx,
    )
else:
    log.warning("OpenFeature is not available on this Python version; skipping feature flag setup.") 

AUDIO_CODEC_SCORES = {codec.lower(): int(score) for codec, score in cfg['AUDIO_CODEC_SCORES'].items()}
VIDEO_CODEC_SCORES = {codec.lower(): int(score) for codec, score in cfg['VIDEO_CODEC_SCORES'].items()}
VIDEO_RESOLUTION_SCORES = {resolution.lower(): int(score) for resolution, score in cfg['VIDEO_RESOLUTION_SCORES'].items()}
FILENAME_SCORE_RULES = tuple((pattern.lower(), int(score)) for pattern, score in cfg['FILENAME_SCORES'].items())
SKIP_LIST = tuple(skip_item.lower() for skip_item in cfg['SKIP_LIST'])

############################################################
# PLEX METHODS
############################################################

def get_dupes(plex_section_name):
    try:
        plex_section = plex.library.section(plex_section_name)
        sec_type = 'episode' if plex_section.type == 'show' else 'movie'
    except Exception:
        log.exception("Exception occurred while trying to lookup the section type for Library: %s", plex_section_name)
        exit(1)

    dupe_search_results = plex_section.search(duplicate=True, libtype=sec_type)

    if not cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
        return dupe_search_results

    return [dupe for dupe in dupe_search_results if all(location == dupe.locations[0] for location in dupe.locations)]


def get_score(media_info):
    score = 0
    # score audio codec
    audio_codec_score = AUDIO_CODEC_SCORES.get(media_info['audio_codec'].lower(), 0)
    score += audio_codec_score
    if audio_codec_score:
        log.debug("Added %d to score for audio_codec being %r", audio_codec_score, media_info['audio_codec'])
    # score video codec
    video_codec_score = VIDEO_CODEC_SCORES.get(media_info['video_codec'].lower(), 0)
    score += video_codec_score
    if video_codec_score:
        log.debug("Added %d to score for video_codec being %r", video_codec_score, media_info['video_codec'])
    # score video resolution
    resolution_score = VIDEO_RESOLUTION_SCORES.get(media_info['video_resolution'].lower(), 0)
    score += resolution_score
    if resolution_score:
        log.debug("Added %d to score for video_resolution being %r", resolution_score, media_info['video_resolution'])
    # score filename
    for filename in media_info['file']:
        filename_score = get_filename_score(os.path.basename(filename).lower())
        score += filename_score
        if filename_score:
            log.debug("Added %d to score for filename %s", filename_score, filename)
    # add bitrate to score
    score += int(media_info['video_bitrate']) * 2
    log.debug("Added %d to score for video bitrate", int(media_info['video_bitrate']) * 2)
    # add duration to score
    score += int(media_info['video_duration']) / 300
    log.debug("Added %d to score for video duration", int(media_info['video_duration']) / 300)
    # add width to score
    score += int(media_info['video_width']) * 2
    log.debug("Added %d to score for video width", int(media_info['video_width']) * 2)
    # add height to score
    score += int(media_info['video_height']) * 2
    log.debug("Added %d to score for video height", int(media_info['video_height']) * 2)
    # add audio channels to score
    score += int(media_info['audio_channels']) * 1000
    log.debug("Added %d to score for audio channels", int(media_info['audio_channels']) * 1000)
    # add file size to score
    if cfg['SCORE_FILESIZE']:
        score += int(media_info['file_size']) / 100000
        log.debug("Added %d to score for total file size", int(media_info['file_size']) / 100000)
    return int(score)

@lru_cache
def get_filename_score(filename):
    total_score = 0
    for filename_keyword, keyword_score in FILENAME_SCORE_RULES:
        if fnmatch(filename, filename_keyword):
            total_score += keyword_score
    return total_score


def get_media_info(item):
    info = {
        'id': 'Unknown',
        'video_bitrate': 0,
        'audio_codec': 'Unknown',
        'audio_channels': 0,
        'video_codec': 'Unknown',
        'video_resolution': 'Unknown',
        'video_width': 0,
        'video_height': 0,
        'video_duration': 0,
        'file': [],
        'multipart': False,
        'file_size': 0
    }
    # get id
    try:
        info['id'] = item.id
    except AttributeError:
        log.debug("Media item has no id")
    # get bitrate
    try:
        info['video_bitrate'] = item.bitrate if item.bitrate else 0
    except AttributeError:
        log.debug("Media item has no bitrate")
    # get video codec
    try:
        info['video_codec'] = item.videoCodec if item.videoCodec else 'Unknown'
    except AttributeError:
        log.debug("Media item has no videoCodec")
    # get video resolution
    try:
        info['video_resolution'] = item.videoResolution if item.videoResolution else 'Unknown'
    except AttributeError:
        log.debug("Media item has no videoResolution")
    # get video height
    try:
        info['video_height'] = item.height if item.height else 0
    except AttributeError:
        log.debug("Media item has no height")
    # get video width
    try:
        info['video_width'] = item.width if item.width else 0
    except AttributeError:
        log.debug("Media item has no width")
    # get video duration
    try:
        info['video_duration'] = item.duration if item.duration else 0
    except AttributeError:
        log.debug("Media item has no duration")
    # get audio codec
    try:
        info['audio_codec'] = item.audioCodec if item.audioCodec else 'Unknown'
    except AttributeError:
        log.debug("Media item has no audioCodec")
    # get audio channels
    try:
        for part in item.parts:
            for stream in part.audioStreams():
                if stream.channels:
                    log.debug(f"Added {stream.channels} channels for {stream.title if stream.title else 'Unknown'} audioStream")
                    info['audio_channels'] += stream.channels
        if info['audio_channels'] == 0:
            info['audio_channels'] = item.audioChannels if item.audioChannels else 0

    except AttributeError:
        log.debug("Media item has no audioChannels")

    # is this a multi part (cd1/cd2)
    if len(item.parts) > 1:
        info['multipart'] = True
    for part in item.parts:
        info['file'].append(part.file)
        info['file_size'] += part.size if part.size else 0

    return info


def delete_item(show_key, media_id):
    delete_url = urljoin(cfg['PLEX_SERVER'], '%s/media/%d' % (show_key, media_id))
    log.debug("Sending DELETE request to %r" % delete_url)
    if requests.delete(delete_url, headers={'X-Plex-Token': cfg['PLEX_TOKEN']}).status_code == 200:
        log.info("\t\tDeleted media item: %r" % media_id)
    else:
        log.info("\t\tError deleting media item: %r" % media_id)


############################################################
# MISC METHODS
############################################################

decision_filename = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), 'decisions.log')

def write_decision(title=None, keeping=None, removed=None):
    lines = []
    if title:
        lines.append('\nTitle    : %s\n' % title)
    if keeping:
        lines.append('\tKeeping  : %r\n' % keeping)
    if removed:
        lines.append('\tRemoving : %r\n' % removed)

    with open(decision_filename, 'a') as fp:
        fp.writelines(lines)
    return


def should_skip(files):
    return any(should_skip_path(str(files_item).lower()) for files_item in files)


@lru_cache
def should_skip_path(file_path):
    return any(skip_item in file_path for skip_item in SKIP_LIST)


@lru_cache
def millis_to_string(millis):
    """ reference: https://stackoverflow.com/a/35990338 """
    try:
        seconds = (millis / 1000) % 60
        seconds = int(seconds)
        minutes = (millis / (1000 * 60)) % 60
        minutes = int(minutes)
        hours = (millis / (1000 * 60 * 60)) % 24
        return "%02d:%02d:%02d" % (hours, minutes, seconds)
    except Exception:
        log.exception(f"Exception occurred converting {millis} millis to readable string: ")
    return "%d milliseconds" % millis


@lru_cache
def bytes_to_string(size_bytes):
    """
    reference: https://stackoverflow.com/a/6547474
    """
    try:
        if size_bytes == 1:
            return "1 byte"
        suffixes_table = [('bytes', 0), ('KB', 0), ('MB', 1), ('GB', 2), ('TB', 2), ('PB', 2)]

        num = float(size_bytes)
        for suffix, precision in suffixes_table:
            if num < 1024.0:
                break
            num /= 1024.0
        if precision == 0:
            formatted_size = "%d" % num
        else:
            formatted_size = str(round(num, ndigits=precision))
        return f"{formatted_size} {suffix}"
    except Exception:
        log.exception(f"Exception occurred converting {size_bytes} bytes to readable string: ")

    return "%d bytes" % size_bytes


@lru_cache
def kbps_to_string(size_kbps):
    try:
        if size_kbps < 1024:
            return "%d Kbps" % size_kbps
        else:
            return "{:.2f} Mbps".format(size_kbps / 1024.)
    except Exception:
        log.exception(f"Exception occurred converting {size_kbps} Kbps to readable string: ")
    return "%d Bbps" % size_kbps


def build_tabulated(parts, items):
    headers = ['choice', 'score', 'id', 'file', 'size', 'duration', 'bitrate', 'resolution',
               'codecs']
    if cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
        headers.remove('score')

    part_data = []

    for choice, item_id in items.items():
        # add to part_data
        tmp = []
        for k in headers:
            if 'choice' in k:
                tmp.append(choice)
            elif 'score' in k:
                tmp.append(str(format(parts[item_id][k], ',d')))
            elif 'id' in k:
                tmp.append(parts[item_id][k])
            elif 'file' in k:
                tmp.append(parts[item_id][k])
            elif 'size' in k:
                tmp.append(bytes_to_string(parts[item_id]['file_size']))
            elif 'duration' in k:
                tmp.append(millis_to_string(parts[item_id]['video_duration']))
            elif 'bitrate' in k:
                tmp.append(kbps_to_string(parts[item_id]['video_bitrate']))
            elif 'resolution' in k:
                tmp.append("%s (%d x %d)" % (parts[item_id]['video_resolution'], parts[item_id]['video_width'],
                                             parts[item_id]['video_height']))
            elif 'codecs' in k:
                tmp.append("%s, %s x %d" % (parts[item_id]['video_codec'], parts[item_id]['audio_codec'],
                                            parts[item_id]['audio_channels']))
        part_data.append(tmp)
    return headers, part_data


def process_section(section):
    dupes = get_dupes(section)
    section_results = {}
    for item in dupes:
        if item.type == 'episode':
            title = "%s - %02dx%02d - %s" % (
                item.grandparentTitle, int(item.parentIndex), int(item.index), item.title)
        elif item.type == 'movie':
            title = item.title
        else:
            title = 'Unknown'

        log.info("Processing: %r", title)
        parts = {}
        for part in item.media:
            part_info = get_media_info(part)
            if not cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
                part_info['score'] = get_score(part_info)
            part_info['show_key'] = item.key
            log.info("ID: %r - Score: %s - Meta:\n%r", part.id, part_info.get('score', 'N/A'),
                     part_info)
            parts[part.id] = part_info
        section_results[title] = parts
    return section, len(dupes), section_results


############################################################
# MAIN
############################################################

if __name__ == "__main__":
    log.info(r"""
        _                 _                   __ _           _
    _ __ | | _____  __   __| |_   _ _ __   ___ / _(_)_ __   __| | ___ _ __
    | '_ \| |/ _ \ \/ /  / _` | | | | '_ \ / _ \ |_| | '_ \ / _` |/ _ \ '__|
    | |_) | |  __/>  <  | (_| | |_| | |_) |  __/  _| | | | | (_| |  __/ |
    | .__/|_|\___/_/\_\  \__,_|\__,_| .__/ \___|_| |_|_| |_|\__,_|\___|_|
    |_|                             |_|

    #########################################################################
    # Author:   l3uddz                                                      #
    # URL:      https://github.com/l3uddz/plex_dupefinder                   #
    # --                                                                    #
    #         Part of the Cloudbox project: https://cloudbox.works          #
    #########################################################################
    #                   GNU General Public License v3.0                     #
    #########################################################################
        """)
    log.info("Initialized")

    # Setup PlexServer object
    try:
        plex = PlexServer(cfg['PLEX_SERVER'], cfg['PLEX_TOKEN'])
    except Exception as e:
        log.exception("Exception connecting to server %r with token %r", cfg.get('PLEX_SERVER'), cfg.get('PLEX_TOKEN'))
        log.error(f"Exception connecting to {cfg.get('PLEX_SERVER')} with token: {cfg.get('PLEX_TOKEN')}")
        log.error(f"Error: {e}")
        sys.exit(1)

    process_later = {}
    # process sections
    log.info("Finding dupes...")
    max_workers = min(len(cfg['PLEX_LIBRARIES']), os.cpu_count() or 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        section_futures = {
            section: executor.submit(process_section, section)
            for section in cfg['PLEX_LIBRARIES']
        }
        for section in cfg['PLEX_LIBRARIES']:
            _, dupe_count, section_results = section_futures[section].result()
            log.info("Found %d dupes for section %r" % (dupe_count, section))
            process_later.update(section_results)
    time.sleep(5)
    for item, parts in process_later.items():
        if not cfg['AUTO_DELETE']:
            partz = {}
            # manual delete
            log.info("\nWhich media item do you wish to keep for %r ?\n" % item)

            sort_key = None
            sort_order = None

            if cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
                sort_key = "id"
                sort_order_reverse = False
            else:
                sort_key = "score"
                sort_order_reverse = True

            media_items = {}
            best_item = None
            for pos, (media_id, part_info) in enumerate(collections.OrderedDict(
                    sorted(parts.items(), key=lambda x: x[1][sort_key], reverse=sort_order_reverse)).items(), start=1):
                if pos == 1:
                    best_item = part_info
                media_items[pos] = media_id
                partz[media_id] = part_info

            headers, data = build_tabulated(partz, media_items)
            log.info(tabulate(data, headers=headers))

            keep_item = input("\nChoose item to keep (0 or s = skip | 1 or b = best): ")
            if (keep_item.lower() != 's') and (keep_item.lower() == 'b' or 0 < int(keep_item) <= len(media_items)):
                write_decision(title=item)
                for media_id, part_info in parts.items():
                    if keep_item.lower() == 'b' and best_item is not None and best_item == part_info:
                        log.info("\tKeeping  : %r" % media_id)
                        write_decision(keeping=part_info)
                    elif keep_item.lower() != 'b' and len(media_items) and media_id == media_items[int(keep_item)]:
                        log.info("\tKeeping  : %r" % media_id)
                        write_decision(keeping=part_info)
                    else:
                        log.info("\tRemoving : %r" % media_id)
                        delete_item(part_info['show_key'], media_id)
                        write_decision(removed=part_info)
                        time.sleep(2)
            elif keep_item.lower() == 's' or int(keep_item) == 0:
                log.info("Skipping deletion(s) for %r" % item)
            else:
                log.info("Unexpected response, skipping deletion(s) for %r" % item)
        else:
            # auto delete
            print("\nDetermining best media item to keep for %r ..." % item)
            keep_score = 0
            keep_id = None

            if cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
                # select lowest id to keep
                for media_id, part_info in parts.items():
                    if keep_score == 0 and keep_id is None:
                        keep_score = int(part_info['id'])
                        keep_id = media_id
                    elif int(part_info['id']) < keep_score:
                        keep_score = part_info['id']
                        keep_id = media_id
            else:
                # select highest score to keep
                for media_id, part_info in parts.items():
                    if int(part_info['score']) > keep_score:
                        keep_score = part_info['score']
                        keep_id = media_id

            if keep_id:
                # delete other items
                write_decision(title=item)
                for media_id, part_info in parts.items():
                    if media_id == keep_id:
                        log.info("\tKeeping  : %r - %r" % (media_id, part_info['file']))
                        write_decision(keeping=part_info)
                    else:
                        log.info("\tRemoving : %r - %r" % (media_id, part_info['file']))
                        if should_skip(part_info['file']):
                            log.info("\tSkipping removal of this item as there is a match in SKIP_LIST")
                            continue
                        delete_item(part_info['show_key'], media_id)
                        write_decision(removed=part_info)
                        time.sleep(2)
            else:
                log.info("Unable to determine best media item to keep for %r", item)

    import time
    import signal
    import threading

    stop_flag = threading.Event()

    def handle_sigterm(signum, frame):
        stop_flag.set()

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    start = time.time()
    while time.time() - start < 600 and not stop_flag.is_set():
        time.sleep(1)
                
