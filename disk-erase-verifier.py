#!/usr/bin/python3

import argparse
import codecs
import csv
import datetime
import json
import os
import random
import subprocess
import shutil
import string
import sys
import time

def human_size(n, units=[' bytes','KB','MB','GB','TB', 'PB', 'EB']):
    """ Returns a human readable string reprentation of n"""
    if n is None:
        return None
    if n < 900:
        return str(n) + units[0]
    elif (n < 10240) and (n % 1024 != 0):
        return "%.2f%s" % (n / 1024.0, units[1])
    else:
        return human_size(n>>10, units[1:])

def open_disk(drive):
    return open(drive, "rb")

DRIVE_INFO_CACHE = {}

def get_drives():
    drives = []

    if os.name == 'nt':
        # See https://stackoverflow.com/questions/827371/is-there-a-way-to-list-all-the-available-drive-letters-in-python
        # This gets mounted partitions. We want phyiscal drives.
        # from ctypes import windll
        # bitmask = windll.kernel32.GetLogicalDrives()
        # for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        #     if bitmask & 1:
        #         drives.append(r"\\.\%s:" % (letter,))
        #     bitmask >>= 1
        # Better: wmi.WMI.Win32_PhysicalMedia() ?
        p = subprocess.run(["wmic", "diskdrive", "get", "/format:csv"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = p.stdout.decode("utf-8").split("\n")
        for row in csv.DictReader(l for l in lines if l.strip() != ""):
            drives.append(row["DeviceID"])
            DRIVE_INFO_CACHE[row["DeviceID"]] = row

    if os.name == "posix":
        # On Linux we could read /dev/disk/by-id ourselves. But this is easier.
        p = subprocess.run(["lsblk", "--json", "--output-all"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for d in json.loads(p.stdout.decode("utf-8")).get("blockdevices", []):
            if d.get("type", "") == "loop":
                continue
            dev = "/dev/%s" % (d["kname"],)
            drives.append(dev)
            DRIVE_INFO_CACHE[dev] = d
            # Make the data match Windows a bit
            # Don't copy "size" to "Size" though, as it isn't precise enough
            d["SerialNumber"] = d["serial"]
            d["Model"] = d["model"]

    return drives


def get_info(drive, key, f):
    info = DRIVE_INFO_CACHE.setdefault(drive, {})

    if key not in info:
        info[key] = f(drive)
    return info[key]

def get_size(drive):
    def internal_get_size(drive):
        with open_disk(drive) as d:
            return d.seek(0, 2)
    try:
        return int(get_info(drive, "Size", internal_get_size))
    except:
        return None

def format_pattern(pattern):
    for i in [1, 2, 4, 8, 16]:
        if pattern == pattern[:i] * (len(pattern) // i):
            return "0x" + codecs.encode(pattern[:i], "hex").decode("ascii")
    return "<pattern too long>"

def check_blocks(d, pattern, start, count, checked, timeout = None, mincount = None):
    if start is None:
        start = d.tell() // len(pattern)

    t0 = time.time()
    done = 0

    for pos in range(start, start+count):
        if pos in checked:
            print("Skipping: %s" % (pos,))
            continue

        d.seek(pos * len(pattern), 0)
        got = d.read(len(pattern))
        if got != pattern:
            #if len(got) != len(pattern):
            #    print("DEBUG: short read of %s bytes at %s" % (len(got), pos * len(pattern)))
            #else:
            #    print("DEBUG: got %s, expected %s" % (format_pattern(got), format_pattern(pattern)))
            return pos

        checked.add(pos)
        done += 1
 
        if (timeout is not None) and (done >= mincount) and (time.time() > t0 + timeout):
            break

    return None

def is_erased(drive):
    try:
        d = open_disk(drive)
        pattern = d.read(4096)

        checked = set([0])

        size = get_size(drive)
        blocks = size // len(pattern)
        if size % len(pattern) != 0:
            blocks+=1

        t0 = time.time()

        diff = None

        check_max = min(blocks // 2, 8193) # Check up to 32MB at a little bit at the beginning and end of the disk
        check_min = min(blocks // 2, ((2048*1024) // len(pattern)) + 1) # Check a minimum of 2MB and a little bit at the beginning and end of the disk

        diff = check_blocks(d, pattern, None, check_max, checked, timeout = 0.2, mincount = ((2048*1024) // len(pattern) + 1))
        checked_start = len(checked)

        if diff is None:
            # Now check the same at the end
            diff = check_blocks(d, pattern, blocks - checked_start, checked_start, checked)

        # Now check an evenly distributed sample of blocks over the entire size of the disk
        check = checked_start
        while (diff is None) and (check < (blocks - checked_start)):
            diff = check_blocks(d, pattern, check, 1, checked)
            check += ((blocks - (checked_start * 2)) // checked_start) 

        # Now a random sample, again using the same number of blocks.
        # Sorting the list might help performance on spinning disks
        random_list = [random.randint(checked_start, blocks - checked_start) for i in range(checked_start)]
        for block in sorted(random_list):
            if diff is not None:
                break
            diff = check_blocks(d, pattern, block, 1, checked)

        if diff is not None:
            return (False, "No, %s block number %s doesn't match start of disk" % (human_size(len(pattern)), diff))

        return (True, "Yes, checked %s (%.02f%%) including first %s. Pattern: %s" % (human_size(len(checked) * len(pattern)), (len(checked) * 100.0) / blocks, human_size(checked_start * len(pattern)), format_pattern(pattern)))
    except Exception as e:
        return (False, "ERROR: %s" % (e,))


parser = argparse.ArgumentParser(
                    prog = 'disk-erase-verifier.py',
                    description = 'Verifies that disks do not contain data',
                    epilog = '')
parser.add_argument('--json', action='store_true', help="output JSON data")
args = parser.parse_args()

drives = get_drives()

output_data = {}

print("Disk erase verifier run at " + str(datetime.datetime.now()))
print()

# On Linux add dmidecode | grep -A 4 "^System Information"

for drive in drives:
    drive_meta = {"model":  get_info(drive, "Model", lambda d: "?"),
                  "serial": get_info(drive, "SerialNumber", lambda d: "?"),
                  "capacity": get_size(drive)}
    if drive_meta["capacity"] == 0:
        continue
    if not args.json:
        print("Drive %s: %s" % (drive, drive_meta["model"]))
        print("  Serial: %s" % (drive_meta["serial"],))
        print("  Capacity: %s" % (human_size(drive_meta["capacity"]),))
    drive_checks = {}
    drive_checks["erased"] = is_erased(drive)
    if not args.json:
        print("  Erased: " + drive_checks["erased"][1])
        print()
    output_data[drive] = {"metadata": drive_meta, "checks": drive_checks}

if args.json:
    json.dump(output_data, sys.stdout)
