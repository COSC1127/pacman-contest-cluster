#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
This script is to run a tournament between teams of agents for the Pacman package developed by
John DeNero (denero@cs.berkeley.edu) and Dan Klein (klein@cs.berkeley.edu) at UC Berkeley.

After running the tournament, the script generates a report in HTML. The report is, optionally,
uploaded to a specified server via scp.
                    
The script was developed for RMIT COSC1125/1127 AI course in Semester 1, 2017 by A/Prof. Sebastian Sardina and PhD
student Marco Tamassia. The script is in turn based on an original script from Dr. Nir Lipovetzky.
"""

#  ----------------------------------------------------------------------------------------------------------------------
# Import future stuff (syntax equivalent to Python 3)

from __future__ import print_function
from future.utils import iteritems

#  ----------------------------------------------------------------------------------------------------------------------
# Import standard stuff

import os
import re
import sys
import datetime
import argparse
import json
import shutil
import zipfile
import logging
import glob
import csv
import tarfile
import random
# noinspection PyCompatibility
import commands
from itertools import combinations
from cluster_manager import ClusterManager, Job, Host, TransferableFile



# logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.DEBUG, datefmt='%a, %d %b %Y %H:%M:%S')
logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, datefmt='%a, %d %b %Y %H:%M:%S')


# ----------------------------------------------------------------------------------------------------------------------
# Verify all necessary packages are present

missing_packages = []
try:
    import iso8601
except:
    missing_packages.append('iso8601')

try:
    from pytz import timezone
except:
    missing_packages.append('pytz')

try:
    # Used to prompt the password without echoing
    from getpass import getpass
except:
    missing_packages.append('getpass')

try:
    # Used to establish ssh connections
    import paramiko
except:
    missing_packages.append('paramiko')

if missing_packages:
    print('Some packages are missing. Please, run `pip install %s`' % ' '.join(missing_packages))
    if 'paramiko' in missing_packages:
        print('Note that you may need to install libssl-dev with `sudo apt-get install libssl-dev`')
    sys.exit(1)

# ----------------------------------------------------------------------------------------------------------------------
# Load settings either from config.json or from the command line

def load_settings():
    DEFAULT_MAX_STEPS = 1200
    DEFAULT_FIXED_LAYOUTS = 3
    DEFAULT_RANDOM_LAYOUTS = 4
    DEFAULT_CONFIG_FILE = 'config.json'

    parser = argparse.ArgumentParser(
        description='This script is to run a tournament between teams of agents for the Pacman package developed by '
                    'John DeNero (denero@cs.berkeley.edu) and Dan Klein (klein@cs.berkeley.edu) at UC Berkeley.\n'
                    '\n'
                    'After running the tournament, the script generates a report in HTML. The report is, optionally, '
                    'uploaded to a specified server via scp.\n'
                    '\n'
                    'The parameters are saved in config.json, so it is only necessary to pass them the first time or '
                    'if they have to be updated.\n'
                    '\n'
                    'The script was developed for RMIT COSC1125/1127 AI course in 2017 (A/Prof. Sebastian Sardina), '
                    'and is based on an original script from Dr. Nir Lipovetzky.'
        )

    parser.add_argument(
        '--config-file',
        help='configuration file to use (default: {default})'.format(default=DEFAULT_CONFIG_FILE),
    )
    parser.add_argument(
        '--organizer',
        help='name of the organizer of the contest'
    )
    parser.add_argument(
        '--output-path',
        help='output directory'
    )
    parser.add_argument(
        '--workers-file-path',
        help='json file with workers details'
    )
    parser.add_argument(
        '--teams-root',
        help='directory containing the zip files of the teams. Files have to be of the form s<student no>_TIMESTAMP.zip;'
             ' for example s9999999_2017-05-13T20:32:43.342000+10:00.zip'
    )
    parser.add_argument(
        '--include-staff-team',
        help='if passed, the staff team will be included (it should sit in a directory called staff_name)',
        action='store_true'
    )
    parser.add_argument(
        '--compress-logs',
        help='if passed, the logs will be compressed in a tar.gz file; otherwise, they will just be archived in a tar file',
        action='store_true'
    )
    parser.add_argument(
        '--max-steps',
        help='the limit on the number of steps for each game (default: {default})'.format(default=DEFAULT_MAX_STEPS),
    )
    parser.add_argument(
        '--no-fixed-layouts',
        help='number of (random) layouts to use from a given fix set (default: {default})'.format(default=DEFAULT_FIXED_LAYOUTS),
        default=3,
    )
    parser.add_argument(
        '--no-random-layouts',
        help='number of random layouts to use (default: {default})'.format(default=DEFAULT_RANDOM_LAYOUTS),
    )
    parser.add_argument(
        '--team-names-file',
        help='the path of the csv that contains (at least) two columns headed "STUDENT_ID" and "TEAM_NAME", used to match submissions with teams',
    )
    parser.add_argument(
        '--allow-non-registered-students',
        help='if passed, students without a team are still allowed to participate',
        action='store_true'
    )
    parser.add_argument(
        '--build-config-file',
        help='if passed, config.json file will be generated with current options',
        action = 'store_true'
    )
    args = parser.parse_args()


    # First get the options from the configuration file if available
    if not args.config_file is None:
        if os.path.exists(args.config_file):
            with open(args.config_file, 'r') as f:
                settings = json.load(f)
                logging.debug('Configuration file loaded')
        else:
            logging.error('Configuration file selected not available')
            settings = {}
    else:
        settings = {}

    # if given, set the parameters as per command line options (may override config file)
    if args.organizer:
        settings['organizer'] = args.organizer
    if args.compress_logs:
        settings['compress_logs'] = args.compress_logs
    if args.include_staff_team:
        settings['include_staff_team'] = args.include_staff_team
    elif 'include_staff_team' not in set(settings.keys()):
        settings['include_staff_team'] = False
    if args.teams_root:
        settings['teams_root'] = args.teams_root
    if args.output_path:
        settings['output_path'] = args.output_path
    if args.no_fixed_layouts:
        settings['no_fixed_layouts'] = int(args.no_fixed_layouts)
    if args.no_random_layouts:
        settings['no_random_layouts'] = int(args.no_random_layouts)
    if args.max_steps:
        settings['max_steps'] = int(args.max_steps)
    elif 'max_steps' not in set(settings.keys()):
        settings['max_steps'] = DEFAULT_MAX_STEPS
    if args.team_names_file:
        settings['team_names_file'] = args.team_names_file
    if args.workers_file_path:
        settings['workers_file_path'] = args.workers_file_path
    settings['allow_non_registered_students'] = args.allow_non_registered_students

    logging.info('Script will run with this configuration: %s' % settings)


    missing_parameters = {'organizer'} - set(settings.keys())
    if missing_parameters:
        logging.error('Missing parameters: %s. Aborting.' % list(sorted(missing_parameters)))
        parser.print_help()
        sys.exit(1)

    # dump current config files into configuration file if requested to do so
    if args.build_config_file:
        logging.info('Dumping current options to file %s' % args.config_file)
        with open(args.config_file, 'w') as f:
            json.dump(settings, f, sort_keys=True, indent=4, separators=(',', ': '))

    return settings

# ----------------------------------------------------------------------------------------------------------------------

class ContestRunner:

    ENV_DIR = 'contest'
    CONTEST_ZIP_FILE = 'contest.zip'
    LAYOUTS_ZIP_FILE = 'layouts.zip'
    STAFF_TEAM_ZIP_FILE = 'staff_team.zip'
    TEAMS_SUBDIR = 'teams'
    RESULTS_DIR = 'results'
    WWW_DIR = 'www'
    TIMEZONE = timezone('Australia/Melbourne')
    SUBMISSION_FILENAME_PATTERN = re.compile(r'^(s\d+)_(.+)?\.zip$')    # s???????[_datetime].zip
    ENV_ZIP_READY = 'contest_and_teams.zip'

    def __init__(self, teams_root, output_path, include_staff_team, organizer, compress_logs, max_steps,
                 no_fixed_layouts, no_random_layouts, team_names_file, allow_non_registered_students):

        self.max_steps = max_steps

        # unique id for this execution of the contest; used to label logs
        self.contest_run_id = datetime.datetime.now().isoformat()

        # path that contains files that make-up a html navigable web folder
        # self.www_path = self.WWW_DIR
        self.www_path = output_path

        # just used in html as a readable string
        self.organizer = organizer

        # a flag indicating whether to compress the logs
        self.compress_logs = compress_logs

        # name and full path of the directory where the results of this execution will be stored
        self.results_dir_name = 'results_{run_id}'.format(run_id=self.contest_run_id)
        self.results_dir_full_path = os.path.join(self.RESULTS_DIR, self.results_dir_name)
        self.www_dir_full_path = os.path.join(self.www_path, self.results_dir_name)


        if not os.path.exists(self.CONTEST_ZIP_FILE):
            logging.error('File %s could not be found. Aborting.' % self.CONTEST_ZIP_FILE)
            sys.exit(1)

        if not os.path.exists(self.LAYOUTS_ZIP_FILE):
            logging.error('File %s could not be found. Aborting.' % self.LAYOUTS_ZIP_FILE)
            sys.exit(1)

        # Setup Pacman CTF environment by extracting it from a clean zip file
        self.layouts = None
        self._prepare_platform(self.CONTEST_ZIP_FILE, self.LAYOUTS_ZIP_FILE, self.ENV_DIR, no_fixed_layouts, no_random_layouts)

        # Setup all of the teams
        teams_dir = os.path.join(self.ENV_DIR, self.TEAMS_SUBDIR)
        if os.path.exists(teams_dir):
            shutil.rmtree(teams_dir)
        os.makedirs(teams_dir)

        # Get all team name mapping from mapping file
        self.team_names = self._load_teams(team_names_file)
        # Setup all team directories under contest/team subdir for contest (copy content in .zip to team dirs)
        self.teams = []
        self.submission_times = {}
        for submission_zip_file in os.listdir(teams_root):
            if submission_zip_file.endswith(".zip"):
                team_zip_path = os.path.join(teams_root, submission_zip_file)
                self._setup_team(team_zip_path, teams_dir, allow_non_registered_students=allow_non_registered_students)

        # Add the staff team, if necessary
        if include_staff_team:
            if not os.path.exists(self.STAFF_TEAM_ZIP_FILE):
                logging.error('File %s could not be found. Aborting.' % self.STAFF_TEAM_ZIP_FILE)
                sys.exit(1)
            self._setup_team(self.STAFF_TEAM_ZIP_FILE, teams_dir, ignore_file_name_format=True)

        # zip directory for transfer to remote workers
        shutil.make_archive(self.ENV_ZIP_READY[:-4], 'zip', self.ENV_DIR)

        self.ladder = {n: [] for n, _ in self.teams}
        self.games = []
        self.errors = {n: 0 for n, _ in self.teams}
        self.team_stats = {n: 0 for n, _ in self.teams}


    def _close(self):
        pass

    def clean_up(self):
        shutil.rmtree(self.RESULTS_DIR)
        # shutil.rmtree(self.ENV_DIR)


    def _generate_run_html(self):
        """
        Generates the html with the results of this run. The html is saved in www/results_<run_id>/results.html.
        """
        os.makedirs(self.www_dir_full_path)

        # tar cvf www/results_<run_id>/recorded_games_<run_id>.tar results/results_<run_id>/*
        tar_full_path = os.path.join(self.www_dir_full_path, 'recorded_games_%s.tar' % self.contest_run_id)

        with tarfile.open(tar_full_path, 'w:gz' if self.compress_logs else 'w') as tar:
            tar.add(self.results_dir_full_path, arcname='/')

        # generate html for this run
        self._calculate_team_stats()
        run_html = self._generate_output()
        # output --> www/results_<run_id>/results.html
        with open(os.path.join(self.www_dir_full_path, 'results.html'), "w") as f:
            print(run_html, file=f)



    def _generate_main_html(self):
        """
        Generates the html that points at the html files of all the runs.
        The html is saved in www/results.html.
        """
        # regenerate main html
        main_html = "<html><body><h1>Results Pacman %s Tournament by Date</h1>" % self.organizer
        for root, dirs, files in os.walk(self.www_path):
            for d in sorted(dirs):
                main_html += "<a href=\"%s/results.html\"> %s  </a> <br>" % (d, d)
        main_html += "<br></body></html>"
        with open(os.path.join(self.www_path, 'results.html'), "w") as f:
            print(main_html, file=f)


    def update_www(self):
        """
        (Re)Generates the html for this run and updates the main html.
        :return: 
        """
        self._generate_run_html()
        self._generate_main_html()

    
    def _parse_result(self, output, red_team_name, blue_team_name):
        """
        Parses the result log of a match.
        :param output: an iterator of the lines of the result log
        :param red_team_name: name of Red team
        :param blue_team_name: name of Blue team
        :return: a tuple containing score, winner, loser and a flag signalling whether there was a bug
        """
        score = 0
        winner = None
        loser = None
        bug = False
        for line in output.splitlines():
            if line.find("wins by") != -1:
                score = abs(int(line.split('wins by')[1].split('points')[0]))
                if line.find('Red') != -1:
                    winner = red_team_name
                    loser = blue_team_name
                elif line.find('Blue') != -1:
                    winner = blue_team_name
                    loser = red_team_name
            if line.find("The Blue team has returned at least ") != -1:
                score = abs(int(line.split('The Blue team has returned at least ')[1].split(' ')[0]))
                winner = blue_team_name
                loser = red_team_name
            elif line.find("The Red team has returned at least ") != -1:
                score = abs(int(line.split('The Red team has returned at least ')[1].split(' ')[0]))
                winner = red_team_name
                loser = blue_team_name
            elif line.find("Tie Game") != -1:
                winner = None
                loser = None
            elif line.find("agent crashed") != -1:
                bug = True
                if line.find("Red agent crashed") != -1:
                    self.errors[red_team_name] += 1
                    winner = blue_team_name
                    loser = red_team_name
                    score = 1
                if line.find("Blue agent crashed") != -1:
                    self.errors[blue_team_name] += 1
                    winner = red_team_name
                    loser = blue_team_name
                    score = 1

        return score, winner, loser, bug
    
    
    def _generate_output(self):
        """
        Generates the output HTML of the report of the tournament and returns it.
        """
        output = "<html><body><h1>Date Tournament %s </h1><br><table border=\"1\">" % self.contest_run_id
        if len(self.teams) == 0:
            output += "No teams participated, thus no match was run."
        elif len(self.teams) == 1:
            output += "Only one team participated, thus no match was run."
        else:
            # First, print a table with the final standing
            output += "<tr><th>Team</th><th>Points</th><th>Win</th><th>Tie</th><th>Lost</th><th>FAILED</th><th>Score Balance</th></tr>"
            for key, (points, wins, draws, loses, errors, sum_score) in \
                    sorted(self.team_stats.items(), key=lambda (k, v): v[0], reverse=True):
                output += "<tr><td align=\"center\">%s</td><td align=\"center\">%d</td><td align=\"center\">%d</td><td align=\"center\" >%d</td><td align=\"center\">%d</td><td align=\"center\" >%d</td><td align=\"center\" >%d</td></tr>" % (
                key, points, wins, draws, loses, errors, sum_score)
            output += "</table>"

            # Second, print each game result
            output += "<br><br> <h2>Games</h2><br><a href=\"recorded_games_%s.tar\">DOWNLOAD RECORDED GAMES!</a><br><table border=\"1\">" % self.contest_run_id
            output += "<tr><th>Team1</th><th>Team2</th><th>Layout</th><th>Score</th><th>Winner</th></tr>"
            for (n1, n2, layout, score, winner) in self.games:
                output += "<tr><td align=\"center\">"
                if winner == n1:
                    output += "<b>%s</b>" % n1
                else:
                    output += "%s" % n1
                output += "</td><td align=\"center\">"
                if winner == n2:
                    output += "<b>%s</b>" % n2
                else:
                    output += "%s" % n2
                if score == 9999:
                    output += "</td><td align=\"center\">%s</td><td align=\"center\" >--</td><td align=\"center\"><b>FAILED</b></td></tr>" % layout
                else:
                    output += "</td><td align=\"center\">%s</td><td align=\"center\" >%d</td><td align=\"center\"><b>%s</b></td></tr>" % (layout, score, winner)

        output += "</table></body></html>"

        return output
    
    
    def _prepare_platform(self, contest_zip_file_path, layouts_zip_file_path, destination, no_fixed_layouts=5, no_random_layouts=3):
        """
        Cleans the given destination directory and prepares a fresh setup to execute a Pacman CTF game within.
        Information on the layouts are saved in the member variable layouts.
        
        :param contest_zip_file_path: the zip file containing the necessary files for the contest (no sub-folder).
        :param layouts_zip_file_path: the zip file containing the layouts to be used for the contest (in the root).
        :param destination: the directory in which to setup the environment.
        :returns: a list of all the layouts
        """
        if os.path.exists(destination):
            shutil.rmtree(destination)
        os.makedirs(destination)
        contest_zip_file = zipfile.ZipFile(contest_zip_file_path)
        contest_zip_file.extractall('.')
        layouts_zip_file = zipfile.ZipFile(layouts_zip_file_path)
        layouts_zip_file.extractall(os.path.join(self.ENV_DIR, 'layouts'))

        # pick no_fixed_layouts layouts from the given set in the zip file
        layouts_available = [file_in_zip[:-4] for file_in_zip in layouts_zip_file.namelist()]
        if no_fixed_layouts >= len(layouts_available):
            self.layouts = layouts_available
        else:
            self.layouts = random.sample(layouts_available, no_fixed_layouts)

        # add a no_random_layouts random layouts
        if no_random_layouts > 0:
            list_random_layouts = ['RANDOM'+str(random.randint(1,9999)) for x in range(0,no_random_layouts)]
            self.layouts = self.layouts + list_random_layouts

    def _setup_team(self, zip_file, destination, ignore_file_name_format=False, allow_non_registered_students=False):
        """
        Extracts team.py from the team submission zip file into a directory inside contest/teams
            If the zip file name is listed in team-name mapping, then name directory with team name
            otherwise name directory after the zip file.
        Information on the teams are saved in the member variable teams.
        
        :param zip_file: the zip file of the team.
        :param destination: the directory where the team directory is to be created.
        :param ignore_file_name_format: if True, an invalid file name format does not cause the team to be ignored.
        In this case, if the file name truly is not respecting the format, the zip file name (minus the .zip part) is
        used as team name. If this function is called twice with files having the same name (e.g., if they are in
        different directories), only the first one is kept.
        :param allow_non_registered_students: if True, students not appearing in the team_names are still allowed (team
        name used is the student id).
        :raises KeyError if the zip file contains multiple copies of team.py, non of which is in the root.
        """
        submission_zip_file = zipfile.ZipFile(zip_file)

        # Get team name from submission: if in self.team_names mapping, then use mapping; otherwise use filename
        match = re.match(self.SUBMISSION_FILENAME_PATTERN, os.path.basename(zip_file))
        submission_time = None
        if match:
            student_id = match.group(1)

            # first get the team of this submission
            if student_id in self.team_names:
                team_name = self.team_names[student_id]
            elif allow_non_registered_students:
                team_name = student_id
            else:
                logging.warning('Student not registered: "%s" (file %s). Skipping' % (student_id, zip_file))
                return

            # next get the submission date (encoded in filename)
            try:
                submission_time = iso8601.parse_date(match.group(2)).astimezone(self.TIMEZONE)
            except iso8601.iso8601.ParseError:
                if not ignore_file_name_format:
                    logging.warning('Team zip file "%s" name has invalid date format. Skipping' % zip_file)
                    return
        else:
            if not ignore_file_name_format:
                logging.warning('Submission zip file "%s" does not correspond to any team. Skipping' % zip_file)
                return
            team_name = os.path.basename(zip_file)[:-4]


        # This submission will be temporarily expanded into team_destination_dir
        team_destination_dir = os.path.join(destination, team_name)
        desired_file = 'myTeam.py'

        if team_name not in self.submission_times:
            submission_zip_file.extractall(team_destination_dir)
            agent_factory = os.path.join(self.TEAMS_SUBDIR, team_name, desired_file)
            self.teams.append((team_name, agent_factory))
            self.submission_times[team_name] = submission_time
        elif submission_time is not None and self.submission_times[team_name] < submission_time:
            shutil.rmtree(team_destination_dir)
            submission_zip_file.extractall(team_destination_dir)
            self.submission_times[team_name] = submission_time


    def _generate_command(self, red_team, blue_team, layout):
        (red_team_name, red_team_agent_factory) = red_team
        (blue_team_name, blue_team_agent_factory) = blue_team
        # TODO: make the -c an option at the meta level to "Catch exceptions and enforce time limits"
        command = 'python capture.py -c -r {red_team_agent_factory} -b {blue_team_agent_factory} -l {layout} -i {steps} -q --record'.format(
                red_team_agent_factory=red_team_agent_factory, blue_team_agent_factory=blue_team_agent_factory,
                layout=layout, steps=self.max_steps)
        return command


    def _analyse_output(self, red_team, blue_team, layout, exit_code, output):
        (red_team_name, red_team_agent_factory) = red_team
        (blue_team_name, blue_team_agent_factory) = blue_team

        # dump the log of the game into file for the game: red vs blue in layout
        log_file_name = '{red_team_name}_vs_{blue_team_name}_{layout}.log'.format(
            layout=layout, run_id=self.contest_run_id, red_team_name=red_team_name, blue_team_name=blue_team_name)
        # results/results_<run_id>/{red_team_name}_vs_{blue_team_name}_{layout}.log
        with open(os.path.join(self.results_dir_full_path, log_file_name), 'w') as f:
            print(output, file=f)

        if exit_code == 0:
            print(' Successful. Log in {output_file}.'.format(output_file=log_file_name))
        else:
            print(' Failed. Log in {output_file}.'.format(output_file=log_file_name))
    

        score, winner, loser, bug = self._parse_result(output, red_team_name, blue_team_name)

        if winner is None:
            self.ladder[red_team_name].append(score)
            self.ladder[blue_team_name].append(score)
        else:
            self.ladder[winner].append(score)
            self.ladder[loser].append(-score)

        #  Next handle replay file
        replay_file_name = '{red_team_name}_vs_{blue_team_name}_{layout}.replay'.format(
            layout=layout, run_id=self.contest_run_id, red_team_name=red_team_name, blue_team_name=blue_team_name)

        replays = glob.glob(os.path.join(self.ENV_DIR, 'replay*'))
        if replays:
            # results/results_<run_id>/{red_team_name}_vs_{blue_team_name}_{layout}.replay
            shutil.move(replays[0], os.path.join(self.results_dir_full_path, replay_file_name))
        if not bug:
            self.games.append((red_team_name, blue_team_name, layout, score, winner))
        else:
            self.games.append((red_team_name, blue_team_name, layout, 9999, winner))
    
    
    def _run_match(self, red_team, blue_team, layout):
        red_team_name, _ = red_team
        blue_team_name, _ = blue_team
        print('Running game %s vs %s (layout: %s).' % (red_team_name, blue_team_name, layout), end='')
        sys.stdout.flush()
        command = self._generate_command(red_team, blue_team, layout)
        logging.info(command)
        exit_code, output = commands.getstatusoutput('cd %s && %s' % (self.ENV_DIR, command))
        self._analyse_output(red_team, blue_team, layout, exit_code, output)


    def run_contest(self):

        os.makedirs(self.results_dir_full_path)

        for red_team, blue_team in combinations(self.teams, r=2):
            for layout in self.layouts:
                self._run_match(red_team, blue_team, layout)


    def _generate_job(self, red_team, blue_team, layout):
        red_team_name, _ = red_team
        blue_team_name, _ = blue_team
        game_command = self._generate_command(red_team, blue_team, layout)
        deflate_command = 'unzip {zip_file} -d {contest_dir} ; chmod +x -R *'.format(zip_file=self.ENV_ZIP_READY, contest_dir=self.ENV_DIR)
        command = '{deflate_command} ; cd {contest_dir} ; {game_command} ; touch {replay_filename}'.format(deflate_command=deflate_command, contest_dir=self.ENV_DIR, game_command=game_command, replay_filename='replay-0')
        req_file = TransferableFile(local_path=self.ENV_ZIP_READY, remote_path=self.ENV_ZIP_READY)
        replay_file_name = '{red_team_name}_vs_{blue_team_name}_{layout}.replay'.format(layout=layout, run_id=self.contest_run_id, red_team_name=red_team_name, blue_team_name=blue_team_name)
        ret_file = TransferableFile(local_path=os.path.join(self.results_dir_full_path, replay_file_name), remote_path=os.path.join(self.ENV_DIR, 'replay-0'))
        return Job(command=command, required_files=[req_file], return_files=[ret_file], id=(red_team, blue_team, layout))


    def _analyse_all_outputs(self, results):
        for (red_team, blue_team, layout), exit_code, output, error in results:
            self._analyse_output(red_team, blue_team, layout, exit_code, output + error)


    def run_contest_remotely(self, hosts):

        os.makedirs(self.results_dir_full_path)

        jobs = []
        for red_team, blue_team in combinations(self.teams, r=2):
            for layout in self.layouts:
                jobs.append(self._generate_job(red_team, blue_team, layout))

        cm = ClusterManager(hosts, jobs)
        results = cm.start()
        self._analyse_all_outputs(results)




    def _calculate_team_stats(self):
        """
        Compute ladder and create html with results. The html is saved in results_<run_id>/results.html.
        """
        for team, scores in iteritems(self.ladder):

            wins = 0
            draws = 0
            loses = 0
            sum_score = 0
            for s in scores:
                if s > 0:
                    wins += 1
                elif s == 0:
                    draws += 1
                else:
                    loses += 1
                sum_score += s

            self.team_stats[team] = [((wins * 3) + draws), wins, draws, loses, self.errors[team], sum_score]

    @staticmethod
    def _load_teams(team_names_file):
        team_names = {}
        with open(team_names_file, 'r') as f:
            reader = csv.reader(f, delimiter=',', quotechar='"')

            student_id_col = None
            team_col = None
            for row in reader:
                if student_id_col is None:
                    student_id_col = row.index('STUDENT_ID')
                    team_col = row.index('TEAM_NAME')

                student_id = row[student_id_col]

                # couple of controls
                team_name = row[team_col].replace('/', 'NOT_FUNNY').replace(' ', '_')
                if team_name == 'staff_team':
                    logging.warning('staff_team is a reserved team name. Skipping.')
                    continue

                if not student_id or not team_name:
                    continue
                team_names[student_id] = team_name
        return team_names



if __name__ == '__main__':
    settings = load_settings()

    # from getpass import getuser

    # prompt for password (for password authentication or if private key is password protected)
    # hosts = [Host(no_cpu=2, hostname='localhost', username=getuser(), password=getpass(), key_filename=None)]
    # use this if no pass is necessary (for private key authentication)
    # hosts = [Host(no_cpu=2, hostname='localhost', username=getuser(), password=None, key_filename=None)]

    with open(settings['workers_file_path'], 'r') as f:
        workers_details = json.load(f)['workers']
    hosts = [Host(no_cpu=w['no_cpu'], hostname=w['hostname'], username=w['username'], password=w['password'], key_filename=w['private_key_file']) for w in workers_details]

    del settings['workers_file_path']
    runner = ContestRunner(**settings)

    # runner.run_contest()

    runner.run_contest_remotely(hosts)

    runner.update_www()

    runner.clean_up()