import argparse
import datetime
import json
import os
import re
import shutil
import subprocess

parser = argparse.ArgumentParser(description='Optimize binning for the given channel.')
parser.add_argument('--input', required=True, type=str, help="input directory")
parser.add_argument('--channel', required=True, type=str, help="channel_year")
parser.add_argument('--output', required=True, type=str, help="output directory")
parser.add_argument('--max-n-bins', required=False, type=int, default=20, help="maximum number of bins")
parser.add_argument('--params', required=False, type=str, default=None,
                    help="algorithm parameters in format param1=value1,param2=value2 ...")
parser.add_argument('--categories', required=False, type=str,
       default='res2b:r,res1b:r,boosted:r,classVBF:r_qqhh,classGGF:r,classttH:r_qqhh,classTT:r_qqhh,classDY:r_qqhh',
       help="comma separated list of categories and pois: cat1:poi1,cat2:poi2 ...")
parser.add_argument('--verbose', required=False, type=int, default=2, help="verbosity level")
parser.add_argument('binning_suggestions', type=str, nargs='*',
                    help="suggestions for binnings to try (e.g. best binnings from the previous round)")

args = parser.parse_args()

def sh_call(cmd, error_message, verbose=0):
    if verbose > 0:
        print('>> {}'.format(cmd))
    returncode = subprocess.call([cmd], shell=True)
    if returncode != 0:
        raise RuntimeError(error_message)

def compare_binnings(b1, b2):
    if b2 is None: return True
    if b1['exp_limit'] != b2['exp_limit']: return b1['exp_limit'] < b2['exp_limit']
    if len(b1['bin_edges']) != len(b2['bin_edges']): return len(b1['bin_edges']) < len(b2['bin_edges'])
    for n in reversed(range(len(b1['bin_edges']))):
        if b1['bin_edges'][n] != b2['bin_edges'][n]: return b1['bin_edges'][n] < b2['bin_edges'][n]
    return False

def getBestBinning(log_file):
    with open(log_file, 'r') as f:
        binnings = json.loads('[' + ', '.join(f.readlines()) + ']')
    best_binning = None
    for binning in binnings:
        if compare_binnings(binning, best_binning):
            best_binning = binning
    return best_binning

categories = []
for cat_entry in args.categories.split(','):
    category, poi = cat_entry.split(':')
    categories.append([category, poi])

output_dir = os.path.join(args.output, args.channel)
workers_dir = os.path.join(output_dir, 'workers')
best_dir = os.path.join(output_dir, 'best')

for d in [args.output, output_dir, workers_dir, best_dir]:
    if not os.path.isdir(d):
        os.mkdir(d)

suggested_binnings = {}

for bs_file in args.binning_suggestions:
    with open(bs_file, 'r') as f:
        bs = json.load(f)
    if args.channel in bs:
        for cat, cat_entry in bs[args.channel].items():
            if cat not in suggested_binnings:
                suggested_binnings[cat] = []
            if type(cat_entry) == list:
                if len(cat_entry) > 0:
                    if type(cat_entry[0]) == list:
                        for binning in cat_entry:
                            suggested_binnings[cat].append(binning)
                    else:
                        suggested_binnings[cat].append(cat_entry)
            elif type(cat_entry) == dict:
                suggested_binnings[cat].append(cat_entry['bin_edges'])
            else:
                raise RuntimeError("Unknown format of suggested binning in '{}'.".format(bs_file))

best_binnings_file = os.path.join(output_dir, 'best.json')
if os.path.isfile(best_binnings_file):
    with open(best_binnings_file, 'r') as f:
        best_binnings = json.load(f)
else:
    best_binnings = { args.channel: {} }

first_cat_index = 0
while first_cat_index < len(categories) and categories[first_cat_index][0] in best_binnings[args.channel]:
    first_cat_index += 1

for cat_index in range(first_cat_index, len(categories)):
    category, poi = categories[cat_index]
    print("Optimising {} {}...".format(args.channel, category))
    input_card = '{}/hh_{}_{}_13TeV.txt'.format(args.input, category, args.channel)
    cat_dir = os.path.join(output_dir, category)
    if not os.path.isdir(cat_dir):
        os.mkdir(cat_dir)

    cat_suggestions = os.path.join(cat_dir, 'to_try.json')
    if category in suggested_binnings:
        with open(cat_suggestions, 'w') as f:
            json.dump(suggested_binnings[category], f)

    cat_log = os.path.join(cat_dir, 'results.json')

    opt_cmd = "python tools/optimize_binning.py --input {} --output {} --workers-dir {} --max-n-bins {} --poi {}" \
              .format(input_card, cat_dir, workers_dir, args.max_n_bins, poi)
    if args.params is not None:
        opt_cmd += ' --params {} '.format(args.params)
    for cat_idx in range(cat_index):
        cat = categories[cat_idx][0]
        other_cat_file = '{}/hh_{}_{}_13TeV.txt'.format(best_dir, cat, args.channel)
        if not os.path.isfile(other_cat_file):
            raise RuntimeError('Datacard "{}" for previous category not found.'.format(other_cat_file))
        opt_cmd += ' {} '.format(other_cat_file)
    sh_call(opt_cmd, "Error while running optimize_binning.py for {}".format(category), args.verbose)
    cat_best = getBestBinning(cat_log)
    if cat_best is None:
        raise RuntimeError("Unable to find best binning for {}".format(category))
    cat_best['poi'] = poi
    bin_edges = ', '.join([ str(edge) for edge in cat_best['bin_edges'] ])
    rebin_cmd = 'python tools/rebinAndRunLimits.py --input {} --output {} --bin-edges "{}" --rebin-only' \
                .format(input_card, best_dir, bin_edges)
    sh_call(rebin_cmd, "Error while appllying best binning for {} {}".format(category, args.channel))
    best_binnings[args.channel][category] = cat_best
    with open(best_binnings_file, 'w') as f:
        f.write('{{\n\t"{}": {{\n'.format(args.channel))
        for cat_idx in range(0, cat_index + 1):
            cat = categories[cat_idx][0]
            f.write('\t\t "{}": '.format(cat))
            json.dump(best_binnings[args.channel][cat], f)
            if cat_idx < cat_index:
                f.write(",")
            f.write("\n")
        f.write("\t}\n}\n")

final_binning_file = output_dir + '.json'
shutil.copy(best_binnings_file, final_binning_file)
print("Binning for {} has been successfully optimised. The results can be found in {}" \
      .format(args.channel, final_binning_file))
