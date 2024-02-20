# Additional Features in Plex Dupefinder

This document outlines the additional features added to the Plex Dupefinder Python application.

## Dry Run Option

The dry run option allows users to simulate the deletion process without actually deleting any files. This option is configured using the `DRY_RUN` parameter in the `config.json` file. When enabled, Plex Dupefinder will log potential delete operations without carrying them out.

Users can also pass the `--dry-run` parameter from the command line to temporarily activate this feature.

## Preventing Plex Optimized Versions

Plex Optimized Versions are automatically excluded from consideration as duplicates. This enhancement improves the accuracy of duplicate detection by excluding files that Plex has optimized for streaming.

However, to address rare instances where Plex incorrectly identifies files as non-optimized versions, users can configure the application to exclude files located under the "Plex Versions" folder from duplicate consideration. This option is controlled by the `SKIP_PLEX_VERSIONS_FOLDER` parameter in the `config.json` file. Media files under the "Plex Versions" folder will be ignored during duplicate identification, providing an additional layer of accuracy to the duplicate detection process.

## Handling Unavailable Media Files

The application now has the ability to remove entries from Plex for media files marked as 'Unavailable'. This functionality is controlled by the `FIND_UNAVAILABLE` parameter in the `config.json` file. Plex Dupefinder will attempt to delete the entry associated with unavailable media files, excluding cases where Plex reports a file size for an unavailable file. This precautionary measure prevents accidental deletion of valid files.

## Deleting Extra .TS Files

A new option, `FIND_EXTRA_TS`, allows users to delete all `.TS` files when a non-`.TS` file is present in the duplicate list. This feature is particularly useful for cleaning up recordings when a higher-quality, non-recorded version is available. By enabling this option, users can ensure that their media library remains clutter-free and optimized for better-quality content.

## Skipping Other Duplicate Checks (Batch Mode)

The `SKIP_OTHER_DUPES` option in the `config.json` file enables users to skip other duplicate checks when not using the `AUTO_DELETE` option. This allows features like `FIND_UNAVAILABLE` and `FIND_OTHER_TS` to be run in batch mode or on a scheduled task unattended, enhancing the flexibility and automation capabilities of Plex Dupefinder.

Alternatively, users can pass the `--skip-other-dupes` parameter from the command line to temporarily activate this feature.

## Docker Image Support

A Dockerfile is provided with Plex Dupefinder, allowing users to build a local image for running Plex Dupefinder instead of installing additional software locally. This enhances portability and simplifies the deployment process, enabling users to run Plex Dupefinder in various environments without additional dependencies. For detailed instructions on building and using the Docker image, please see `Dockerfile_README.md`.
