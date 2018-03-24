#!/usr/bin/env python3.5
import logging
import os
import sys
import time
from enum import Enum

from config import cfg

try:
    from urlparse import urljoin
except ImportError:
    from urllib.parse import urljoin

from plexapi.server import PlexServer
import requests


class SectionType(Enum):
    MOVIE = 1
    TV = 2


############################################################
# INIT
############################################################

# Setup logger
log_filename = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), 'activity.log')
logging.basicConfig(
    filename=log_filename,
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logging.getLogger('urllib3.connectionpool').disabled = True
log = logging.getLogger("Plex_Dupefinder")

# Setup PlexServer object
try:
    plex = PlexServer(cfg.PLEX_SERVER, cfg.PLEX_TOKEN)
except:
    log.exception("Exception connecting to server %r with token %r", cfg.PLEX_SERVER, cfg.PLEX_TOKEN)
    print("Exception connecting to %s with token: %s" % (cfg.PLEX_SERVER, cfg.PLEX_TOKEN))
    exit(1)


############################################################
# PLEX METHODS
############################################################

def get_dupes(plex_section_name, plex_section_type):
    sec_type = 'episode' if plex_section_type == SectionType.TV else 'movie'
    return plex.library.section(plex_section_name).search(duplicate=True,
                                                          libtype=sec_type)


def get_score(media_info):
    score = 0
    # score audio codec
    for codec, codec_score in cfg.CODEC_SCORES.items():
        if codec.lower() == media_info['audio_codec'].lower():
            score += int(codec_score)
            log.debug("Added %d to score for audio_codec being %r", int(codec_score), str(codec))
            break
    # add bitrate to score
    score += int(media_info['video_bitrate'])
    log.debug("Added %d to score for video bitrate", int(media_info['video_bitrate']))
    # add duration to score
    score += int(media_info['video_duration']) / 1000
    log.debug("Added %d to score for video duration", int(media_info['video_duration']) / 1000)
    # add width to score
    score += int(media_info['video_width'])
    log.debug("Added %d to score for video width", int(media_info['video_width']))
    # add height to score
    score += int(media_info['video_height'])
    log.debug("Added %d to score for video height", int(media_info['video_height']))
    # add audio channels to score
    score += int(media_info['audio_channels']) * 1000
    log.debug("Added %d to score for audio channels", int(media_info['audio_channels']) * 1000)
    # add file size to score
    score += int(media_info['file_size']) / 1000
    log.debug("Added %d to score for total file size", int(media_info['file_size']) / 1000)
    return int(score)


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
                    log.debug("Added %d channels for %s audioStream", stream.channels,
                              stream.title if stream.title else 'Unknown')
                    info['audio_channels'] += stream.channels
        if info['audio_channels'] == 0:
            info['audio_channels'] = item.audioChannels if item.audioChannels else 0

    except AttributeError:
        log.debug("Media item has no audioChannels")

    # is this a multi part (cd1/cd2)
    if len(item.parts) > 1:
        info['multipart'] = True
    for part in item.parts:
        info['file'].append(part.file.encode('utf-8'))
        info['file_size'] += part.size

    return info


def delete_item(show_key, media_id):
    delete_url = urljoin(cfg.PLEX_SERVER, '%s/media/%d' % (show_key, media_id))
    log.debug("Sending DELETE request to %r" % delete_url)
    if requests.delete(delete_url, headers={'X-Plex-Token': cfg.PLEX_TOKEN}).status_code == 200:
        print("\t\tDeleted media item %r!" % media_id)
    else:
        print("\t\tError deleting media item %r..." % media_id)


############################################################
# MISC METHODS
############################################################

decision_filename = os.path.join(os.path.dirname(sys.argv[0]), 'decisions.log')


def write_decision(title=None, keeping=None, removed=None):
    lines = []
    if title:
        lines.append('\nTitle: %s\n' % title)
    if keeping:
        lines.append('\tKeeping: %r\n' % keeping)
    if removed:
        lines.append('\tRemoving: %r\n' % removed)

    with open(decision_filename, 'a') as fp:
        fp.writelines(lines)
    return


def should_skip(files):
    for files_item in files:
        for skip_item in cfg.SKIP_LIST:
            if skip_item.lower() in str(files_item).lower():
                return True
    return False


############################################################
# MAIN
############################################################

if __name__ == "__main__":
    print("Initialized")
    process_later = {}
    # process sections
    print("Finding dupes...")
    for section, section_type in cfg.PLEX_SECTIONS.items():
        dupes = get_dupes(section, section_type)
        print("Found %d dupes for section %r" % (len(dupes), section))
        # loop returned duplicates
        for item in dupes:
            if item.type == 'episode':
                title = ("%s - %02dx%02d - %s" % (
                    item.grandparentTitle, int(item.parentIndex), int(item.index), item.title)).encode('utf-8')
            elif item.type == 'movie':
                title = item.title.encode('utf-8')
            else:
                title = 'Unknown'

            log.info("Processing: %r", title)
            # loop returned parts for media item (copy 1, copy 2...)
            parts = {}
            for part in item.media:
                part_info = get_media_info(part)
                part_info['score'] = get_score(part_info)
                part_info['show_key'] = item.key
                log.info("ID: %r - Score: %d - Meta:\n%r", part.id, part_info['score'],
                         part_info)
                parts[part.id] = part_info
            process_later[title] = parts

    # process processed items
    time.sleep(5)
    for item, parts in process_later.items():
        if not cfg.AUTO_DELETE:
            # manual delete
            print("Which media item do you wish to keep for %r" % item)
            for media_id, part_info in parts.items():
                print("\tID: %r - Score: %r - INFO: %r" % (media_id, part_info['score'], part_info))
            keep_id = int(input("Enter ID of item to keep (0 = skip): "))
            if keep_id and keep_id in parts:
                write_decision(title=item)
                for media_id, part_info in parts.items():
                    if media_id == keep_id:
                        print("\tKeeping %r" % media_id)
                        write_decision(keeping=part_info)
                    else:
                        print("\tRemoving %r" % media_id)
                        delete_item(part_info['show_key'], media_id)
                        write_decision(removed=part_info)
                        time.sleep(2)
            else:
                print("Unexpected response, skipping deletion(s) for %r" % item)
        else:
            # auto delete
            print("Determining best media item to keep for %r" % item)
            keep_score = 0
            keep_id = None
            for media_id, part_info in parts.items():
                if int(part_info['score']) > keep_score:
                    keep_score = part_info['score']
                    keep_id = media_id
            if keep_id:
                # delete other items
                write_decision(title=item)
                for media_id, part_info in parts.items():
                    if media_id == keep_id:
                        print("\tKeeping %r: %r" % (media_id, part_info['file']))
                        write_decision(keeping=part_info)
                    else:
                        print("\tRemoving %r: %r" % (media_id, part_info['file']))
                        if should_skip(part_info['file']):
                            print("\tSkipping removal of this item as there is a match in SKIP_LIST")
                            continue
                        delete_item(part_info['show_key'], media_id)
                        write_decision(removed=part_info)
                        time.sleep(2)
            else:
                print("Unable to determine best media item to keep for %r", item)
