#!/usr/bin/python3

import codecs
import csv
import os
import random
import subprocess
import shutil
import string
import time

def human_size(bytes, units=[' bytes','KB','MB','GB','TB', 'PB', 'EB']):
    """ Returns a human readable string reprentation of bytes"""
    return str(bytes) + units[0] if bytes < 1024 else human_size(bytes>>10, units[1:])

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

    # if os.name == "posix" ...

    return drives


def get_info(drive, key, f):
    info = DRIVE_INFO_CACHE.setdefault(drive, {})

    if key not in info:
        info[key] = f(drive)
    return info[key]

def get_size(drive):
    def internal_get_size(drive):
        with open_drive(drive) as d:
            return d.seek(0, 2)
    return int(get_info(drive, "Size", internal_get_size))

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
            if len(got) != len(pattern):
                print("DEBUG: short read of %s bytes at %s" % (len(got), pos * len(pattern)))
            else:
                print("DEBUG: got %s, expected %s" % (format_pattern(got), format_pattern(pattern)))
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
            return "No, bytes starting at %s of %s don't match first" % (diff * len(pattern), len(pattern))

        return "Yes, checked %s (%.02f%%) including first %s. Pattern: %s" % (human_size(len(checked) * len(pattern)), (len(checked) * 100.0) / blocks, human_size(checked_start * len(pattern)), format_pattern(pattern))
    except Exception as e:
        return "ERROR: %s" % (e,)

drives = get_drives()

for drive in drives:
    print("Drive %s: %s" % (drive, get_info(drive, "Model", lambda d: "?")))
    print("  Serial: %s" % (get_info(drive, "SerialNumber", lambda d: "?"),))
    print("  Capacity: %s" % (human_size(get_size(drive)),))
    print("  Erased: " + is_erased(drive))
    print()

