#!/usr/bin/env python3
import collections
import itertools
import logging
import os
import sys
import time
import argparse
import shutil
from fnmatch import fnmatch

from tabulate import tabulate

from config import cfg

try:
    from urlparse import urljoin
except ImportError:
    from urllib.parse import urljoin

from plexapi.server import PlexServer
import requests

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
    plex = PlexServer(cfg['PLEX_SERVER'], cfg['PLEX_TOKEN'])
except:
    log.exception("Exception connecting to server %r with token %r", cfg['PLEX_SERVER'], cfg['PLEX_TOKEN'])
    print(f"Exception connecting to {cfg['PLEX_SERVER']} with token: {cfg['PLEX_TOKEN']}")

    exit(1)


############################################################
# PLEX METHODS
############################################################


def get_dupes(plex_section_name):
    sec_type = get_section_type(plex_section_name)
    dupe_search_results = plex.library.section(plex_section_name).search(duplicate=True, libtype=sec_type)

    dupe_search_results_new = dupe_search_results.copy()
    if cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
        for dupe in dupe_search_results:
            if any(x != dupe.locations[0] for x in dupe.locations):
                dupe_search_results_new.remove(dupe)
    return dupe_search_results_new


def get_section_type(plex_section_name):
    try:
        plex_section_type = plex.library.section(plex_section_name).type
    except Exception:
        log.exception("Exception occurred while trying to lookup the section type for Library: %s", plex_section_name)
        exit(1)
    return 'episode' if plex_section_type == 'show' else 'movie'


def get_score(media_info):
    score = 0
    # score audio codec
    for codec, codec_score in cfg['AUDIO_CODEC_SCORES'].items():
        if codec.lower() == media_info['audio_codec'].lower():
            score += int(codec_score)
            log.debug("Added %d to score for audio_codec being %r", int(codec_score), str(codec))
            break
    # score video codec
    for codec, codec_score in cfg['VIDEO_CODEC_SCORES'].items():
        if codec.lower() == media_info['video_codec'].lower():
            score += int(codec_score)
            log.debug("Added %d to score for video_codec being %r", int(codec_score), str(codec))
            break
    # score video resolution
    for resolution, resolution_score in cfg['VIDEO_RESOLUTION_SCORES'].items():
        if resolution.lower() == media_info['video_resolution'].lower():
            score += int(resolution_score)
            log.debug("Added %d to score for video_resolution being %r", int(resolution_score), str(resolution))
            break
    # score filename
    for filename_keyword, keyword_score in cfg['FILENAME_SCORES'].items():
        for filename in media_info['file']:
            if fnmatch(os.path.basename(filename.lower()), filename_keyword.lower()):
                score += int(keyword_score)
                log.debug("Added %d to score for match filename_keyword %s", int(keyword_score), filename_keyword)
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


def delete_item(media_type, show_key, media_id, mode='plex'):
    """
    Delete a media item from Plex and optionally from disk.
    
    Args:
        media_type (str): Type of media ('movie' or 'episode')
        show_key (str): The show key for the media item
        media_id (int): The ID of the media item to delete
        mode (str): The deletion mode ('plex', 'hard-delete', or 'both')
        
    Returns:
        dict: Dictionary with API and filesystem deletion results
    """
    if media_type == 'movie':
        # For movies, delete the entire metadata record.
        delete_url = urljoin(cfg['PLEX_SERVER'], 'library/metadata/%d' % media_id)
    elif media_type == 'episode':
        # For episodes, if you want to delete the entire episode entry, you could also use the metadata endpoint.
        # Alternatively, if you intend to delete just the media part, you might need:
        delete_url = urljoin(cfg['PLEX_SERVER'], '%s/media/%d' % (show_key, media_id))
    log.debug("Sending DELETE request to %r" % delete_url)
    
    result = {
        'api_success': False,
        'filesystem_success': False
    }
    file_paths = []
    
    # Only perform Plex API deletion if mode is 'plex' or 'both'
    if mode in ['plex', 'both']:
        try:
            start_time = time.time()
            response = requests.delete(delete_url, headers={'X-Plex-Token': cfg['PLEX_TOKEN']})
            elapsed_time = time.time() - start_time
            log.debug("Delete response - Status: %d, Time: %.2fs, URL: %s",
                    response.status_code, elapsed_time, delete_url)
            if response.status_code in (200, 204):
                # Verify the item is actually gone by trying to fetch it
                verification_success = verify_item_deleted(media_type, media_id)
                if verification_success:
                    print("\t\tSuccessfully deleted media item: %r" % media_id)
                    log.info("Successfully deleted media item %r from %r", media_id, show_key)
                    result['api_success'] = True
                else:
                    error_msg = f"API reported success but item {media_id} still exists in Plex"
                    print(f"\t\t{error_msg}")
                    log.error(error_msg)
            else:
                error_msg = f"Failed to delete media item {media_id} (Status: {response.status_code})"
                print(f"\t\t{error_msg}")
                log.error(error_msg)
        except requests.exceptions.RequestException as e:
            error_msg = f"Network error deleting media item {media_id}: {str(e)}"
            print(f"\t\t{error_msg}")
            log.exception(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error deleting media item {media_id}: {str(e)}"
            print(f"\t\t{error_msg}")
            log.exception(error_msg)
    
    # Perform filesystem deletion if mode is 'hard-delete' or 'both'
    # or if mode is 'plex' and API deletion failed
    if mode == 'hard-delete' or mode == 'both' or (mode == 'plex' and not result['api_success']):
        if mode == 'plex' and not result['api_success']:
            print(f"\t\tAPI delete failed, attempting hard delete from disk for media ID {media_id}")
            log.info(f"API delete failed for media ID {media_id}, attempting hard delete from disk")
        else:
            print(f"\t\tPerforming hard delete from disk for media ID {media_id}")
            log.info(f"Performing hard delete from disk for media ID {media_id}")
        
        try:
            file_paths = _get_file_paths_for_media_id(media_id)
            log.debug(f"File paths for media ID {media_id}: {file_paths}")
            
            if file_paths:
                if media_type == 'movie':
                    _handle_movie_deletion(file_paths, media_id)
                else:
                    _handle_episode_deletion(file_paths)
                result['filesystem_success'] = True
            else:
                print("\t\tNo file paths found for hard delete")
                log.warning(f"No file paths found for hard delete of media ID {media_id}")
        except Exception as e:
            error_msg = f"Error during hard delete of media ID {media_id}: {str(e)}"
            print(f"\t\t{error_msg}")
            log.exception(error_msg)
    
    log.info(f"Delete results - API: {result['api_success']}, Filesystem: {result['filesystem_success']}, File paths: {file_paths}")
    return result


def verify_item_deleted(media_type, media_id):
    """
    Verify that an item has been deleted from Plex by attempting to fetch it.
    
    Args:
        media_type (str): Type of media ('movie' or 'episode')
        media_id (int): The ID of the media item to verify
        
    Returns:
        bool: True if the item is confirmed to be deleted (not found), False otherwise
    """
    try:
        # Construct the URL to fetch the item
        fetch_url = urljoin(cfg['PLEX_SERVER'], f'library/metadata/{media_id}')
        log.debug(f"Verifying deletion by fetching: {fetch_url}")
        
        # Add a small delay to allow Plex to process the deletion
        time.sleep(1)
        
        # Try to fetch the item
        response = requests.get(fetch_url, headers={'X-Plex-Token': cfg['PLEX_TOKEN']})
        
        # If we get a 404, the item is gone (success)
        if response.status_code == 404:
            log.debug(f"Verification successful - item {media_id} not found in Plex")
            return True
        else:
            log.warning(f"Verification failed - item {media_id} still exists (status: {response.status_code})")
            return False
    except Exception as e:
        log.exception(f"Error verifying deletion of item {media_id}: {str(e)}")
        # If we can't verify, assume it failed
        return False

# Move this to the top, before any function definitions
process_later = {}

def _get_file_paths_for_media_id(media_id):
    """
    Helper function to get file paths for a given media ID.
    """
    file_paths = []
    log.debug(f"Looking up file paths for media ID {media_id}")
    for item_title, parts_dict in process_later.items():
        for part_id, part_info in parts_dict.items():
            log.debug(f"Checking part_id {part_id} for media_id {media_id}")
            if str(part_id) == str(media_id):
                file_paths = part_info['file']
                log.debug(f"Found file paths for media ID {media_id}: {file_paths}")
                break
    if not file_paths:
        log.warning(f"No file paths found for media ID {media_id}")
    return file_paths


def _handle_movie_deletion(file_paths, media_id):
    """
    Handle deletion of movie files, considering shared directories.
    
    Args:
        file_paths (list): List of file paths to delete
        media_id (int): The ID of the media item
    """
    # Get all file paths for all titles to check for shared directories
    all_files = []
    for parts_dict in process_later.values():
        for part_info in parts_dict.values():
            all_files.extend(part_info['file'])
    
    for file_path in file_paths:
        log.debug(f"Attempting to delete file: {file_path}")
        if not os.path.exists(file_path):
            log.warning(f"File does not exist: {file_path}")
            continue
            
        movie_dir = os.path.dirname(file_path)
        
        # Check if any other files in this directory should be kept
        shared_directory = False
        for other_file in all_files:
            if other_file != file_path and os.path.dirname(other_file) == movie_dir:
                shared_directory = True
                break
        
        try:
            if shared_directory:
                # If directory is shared, just delete the specific file
                print(f"\t\tDetected shared directory. Hard deleting only file: {file_path}")
                log.info(f"Detected shared directory. Hard deleting only file: {file_path}")
                os.remove(file_path)
                log.debug(f"os.remove called for {file_path}")
            else:
                # If directory is not shared, delete the entire directory
                print(f"\t\tHard deleting movie directory: {movie_dir}")
                log.info(f"Hard deleting movie directory: {movie_dir}")
                shutil.rmtree(movie_dir, ignore_errors=True)
                log.debug(f"shutil.rmtree called for {movie_dir}")
            
            # Verify deletion
            if not os.path.exists(file_path):
                print(f"\t\tSuccessfully removed file from disk: {file_path}")
                log.info(f"Successfully removed file from disk: {file_path}")
            else:
                print(f"\t\tFailed to remove file from disk: {file_path}")
                log.error(f"Failed to remove file from disk: {file_path}")
        except PermissionError as e:
            print(f"\t\tPermission error deleting {file_path}: {str(e)}")
            log.error(f"Permission error deleting {file_path}: {str(e)}")
        except OSError as e:
            print(f"\t\tOS error deleting {file_path}: {str(e)}")
            log.error(f"OS error deleting {file_path}: {str(e)}")


def _handle_episode_deletion(file_paths):
    """
    Handle deletion of episode files.
    
    Args:
        file_paths (list): List of file paths to delete
    """
    for file_path in file_paths:
        log.debug(f"Attempting to delete file: {file_path}")
        try:
            print(f"\t\tHard deleting file: {file_path}")
            log.info(f"Hard deleting file: {file_path}")
            os.remove(file_path)
            log.debug(f"os.remove called for {file_path}")
            
            # Verify deletion
            if not os.path.exists(file_path):
                print(f"\t\tSuccessfully removed file from disk: {file_path}")
                log.info(f"Successfully removed file from disk: {file_path}")
            else:
                print(f"\t\tFailed to remove file from disk: {file_path}")
                log.error(f"Failed to remove file from disk: {file_path}")
        except PermissionError as e:
            print(f"\t\tPermission error deleting {file_path}: {str(e)}")
            log.error(f"Permission error deleting {file_path}: {str(e)}")
        except OSError as e:
            print(f"\t\tOS error deleting {file_path}: {str(e)}")
            log.error(f"OS error deleting {file_path}: {str(e)}")

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
    return any(skip_item in str(files_item) for files_item, skip_item in itertools.product(files, cfg['SKIP_LIST']))


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


############################################################
# MAIN
############################################################

def rescan_libraries():
    """
    Trigger a rescan of all Plex libraries that were configured in the settings.
    """
    print("\nTriggering library rescan for all configured libraries...")
    for section in cfg['PLEX_LIBRARIES']:
        try:
            library = plex.library.section(section)
            library.update()
            print(f"Initiated rescan for library: {section}")
            log.info(f"Initiated rescan for library: {section}")
        except Exception as e:
            print(f"Failed to initiate rescan for library {section}: {str(e)}")
            log.error(f"Failed to initiate rescan for library {section}: {str(e)}")
    print("Library rescans initiated. This may take some time to complete.")

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Find and remove duplicate media from Plex")
    parser.add_argument('--mode', choices=['plex', 'hard-delete', 'both'], default='plex',
                        help='Deletion mode: "plex" (remove from Plex only), "hard-delete" (physically delete files), '
                             'or "both" (remove from Plex and delete files)')
    args = parser.parse_args()
    
    print(r"""
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
    if args.mode in ['hard-delete', 'both']:
        print(f"WARNING: {args.mode.upper()} mode enabled - files will be physically removed from disk!")
        
    print("Initialized")
    # process sections
    print("Finding dupes...")
    
    for section in cfg['PLEX_LIBRARIES']:
        dupes = get_dupes(section)
        print("Found %d dupes for section %r" % (len(dupes), section))
        # loop returned duplicates
        for item in dupes:
            if item.type == 'episode':
                title = "%s - %02dx%02d - %s" % (
                    item.grandparentTitle, int(item.parentIndex), int(item.index), item.title)
            elif item.type == 'movie':
                title = item.title
            else:
                title = 'Unknown'

            log.info("Processing: %r", title)
            # loop returned parts for media item (copy 1, copy 2...)
            parts = {}
            for part in item.media:
                part_info = get_media_info(part)
                if not cfg['FIND_DUPLICATE_FILEPATHS_ONLY']:
                    part_info['score'] = get_score(part_info)
                part_info['show_key'] = item.key
                part_info['media_type'] = item.type  # Store the media type (movie or episode)
                log.info("ID: %r - Score: %s - Meta:\n%r", part.id, part_info.get('score', 'N/A'),
                         part_info)
                parts[part.id] = part_info
            process_later[title] = parts

    # Track if any filesystem deletions occurred
    filesystem_deletions_occurred = False
    
    # process processed items
    time.sleep(5)
    total_items = len(process_later)
    for current_index, (item, parts) in enumerate(process_later.items(), 1):
        if not cfg['AUTO_DELETE']:
            partz = {}
            # manual delete
            print("\nWhich media item do you wish to keep for %r ? [%d/%d]\n" % (item, current_index, total_items))

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
            print(tabulate(data, headers=headers))

            keep_item = input("\nChoose item to keep (0 or s = skip | 1 or b = best): ")
            if (keep_item.lower() != 's') and (keep_item.lower() == 'b' or 0 < int(keep_item) <= len(media_items)):
                write_decision(title=item)
                for media_id, part_info in parts.items():
                    # In the manual delete section (around line 470)
                    if keep_item.lower() == 'b' and best_item is not None and best_item == part_info:
                        print("\tKeeping  : %r" % media_id)
                        write_decision(keeping=part_info)
                    elif keep_item.lower() != 'b' and len(media_items) and media_id == media_items[int(keep_item)]:
                        print("\tKeeping  : %r" % media_id)
                        write_decision(keeping=part_info)
                    else:
                        print("\tRemoving : %r" % media_id)
                        result = delete_item(part_info['media_type'], part_info['show_key'], media_id, args.mode)
                        if args.mode in ['hard-delete', 'both'] and result['filesystem_success']:
                            filesystem_deletions_occurred = True
                        write_decision(removed=part_info)
                        time.sleep(2)
                                        
            elif keep_item.lower() == 's' or int(keep_item) == 0:
                print("Skipping deletion(s) for %r" % item)
            else:
                print("Unexpected response, skipping deletion(s) for %r" % item)
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
                        print("\tKeeping  : %r - %r" % (media_id, part_info['file']))
                        write_decision(keeping=part_info)
                    else:
                        print("\tRemoving : %r - %r" % (media_id, part_info['file']))
                        if should_skip(part_info['file']):
                            print("\tSkipping removal of this item as there is a match in SKIP_LIST")
                            continue
                        result = delete_item(part_info['media_type'], part_info['show_key'], media_id, args.mode)
                        if args.mode in ['hard-delete', 'both'] and result['filesystem_success']:
                            filesystem_deletions_occurred = True
                        write_decision(removed=part_info)
                        time.sleep(2)
            else:
                print("Unable to determine best media item to keep for %r", item)
    
    # Rescan libraries if filesystem deletions occurred
    if filesystem_deletions_occurred:
        rescan_libraries()
