#!/bin/bash
# Since plex is having such great troubles deleting files I wrote this little bash script to read the decisions.log and delete the files after it's been run.

inputfile=$1
while read -r line
do
   if [[ "$line" == *"Removing : {"* ]]; then
      var=$(echo "${line}" | grep Removing | sed -E "s/.*'file': \['(.*)'\].*/\1/")
      #var=${var/\/path\/on\/server/\/path/to/local/mount}
      echo "${var}"
      rm "${var}"
   fi
done < "$inputfile"
