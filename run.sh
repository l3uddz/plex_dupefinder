# can be used for automation
rm decisions.log
rm activity.log
echo "Installing dependencies.."
python3 -m pip install -r requirements.txt
echo "Finding dupes.."
python3 plex_dupefinder.py
bash deletefiles.sh decisions.log
