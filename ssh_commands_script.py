#!/usr/bin/python

#  ----------------------------------------------------------------------------------------------------------------------
# Import future stuff (syntax equivalent to Python 3)

from __future__ import print_function
from future.utils import iteritems

#  ----------------------------------------------------------------------------------------------------------------------
# Import standard stuff

import os
import sys
import datetime
import argparse
import json
import shutil
import zipfile
import logging
import glob
import tarfile
# noinspection PyCompatibility
import commands
from itertools import combinations

# ----------------------------------------------------------------------------------------------------------------------
# Verify all necessary packages are present

missing_packages = []
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
# Import class from helper module

from ssh_helper import RunCommand

# ----------------------------------------------------------------------------------------------------------------------
# Load settings either from config.json or from the command line

def load_settings():
    CONFIG_PATH = 'config.json'

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            settings = json.load(f)
    else:
        settings = {}

    parser = argparse.ArgumentParser(
        description='This script is to run a tournament between teams of agents for the Pacman package developed by '
                    'John DeNero (denero@cs.berkeley.edu) and Dan Klein (klein@cs.berkeley.edu) at UC Berkeley.\n'
                    '\n'
                    'After running the tournament, the script generates a report in HTML. The report is, optionally, '
                    'uploaded to a specified server via scp.\n'
                    '\n'
                    'The parameters are saved in config.json, so it is only necessary to pass them the first time or '
                    'if they have to be updated.')

    parser.add_argument(
        '--organizer',
        help='name of the organizer of the contest',
    )
    parser.add_argument(
        '--host',
        help='ssh host'
    )
    parser.add_argument(
        '--user',
        help='username'
    )
    parser.add_argument(
        '--output-path',
        help='output directory',
        default='www'
    )
    parser.add_argument(
        '--teams-root',
        help='directory containing the zip files of the teams',
        default='teams'
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
    args = parser.parse_args()

    if args.organizer:
        settings['organizer'] = args.organizer
    if args.organizer:
        settings['host'] = args.host
    if args.organizer:
        settings['user'] = args.user
    if args.compress_logs:
        settings['compress_logs'] = args.compress_logs
    if args.include_staff_team:
        settings['include_staff_team'] = args.include_staff_team
    if args.teams_root:
        settings['teams_root'] = args.teams_root


    missing_parameters = {'organizer'} - set(settings.keys())
    if missing_parameters:
        print('Missing parameters: %s. Aborting.' % list(sorted(missing_parameters)))
        parser.print_help()
        sys.exit(1)

    with open(CONFIG_PATH, 'w') as f:
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
    MAX_STEPS = 1200
    
    def __init__(self, teams_root, include_staff_team, organizer, compress_logs,
                 host=None, user=None):

        self.run = RunCommand()
        if host is not None:
            self.run.do_add_host("%s,%s,%s" % (host, user, getpass()))
            self.run.do_connect()

        # unique id for this execution of the contest; used to label logs
        self.contest_run_id = datetime.datetime.now().isoformat()

        # path that contains files that make-up a html navigable web folder
        self.www_path = self.WWW_DIR

        # just used in html as a readable string
        self.organizer = organizer

        # a flag indicating whether to compress the logs
        self.compress_logs = compress_logs

        # name and full path of the directory where the results of this execution will be stored
        self.results_dir_name = 'results_{run_id}'.format(run_id=self.contest_run_id)
        self.results_dir_full_path = os.path.join(self.RESULTS_DIR, self.results_dir_name)
        self.www_dir_full_path = os.path.join(self.WWW_DIR, self.results_dir_name)


        if not os.path.exists(self.CONTEST_ZIP_FILE):
            logging.error('File %s could not be found. Aborting.' % self.CONTEST_ZIP_FILE)
            sys.exit(1)

        if not os.path.exists(self.LAYOUTS_ZIP_FILE):
            logging.error('File %s could not be found. Aborting.' % self.LAYOUTS_ZIP_FILE)
            sys.exit(1)

        # Setup Pacman CTF environment by extracting it from a clean zip file
        self.layouts = None
        self._prepare_platform(self.CONTEST_ZIP_FILE, self.LAYOUTS_ZIP_FILE, self.ENV_DIR)

        # Setup all of the teams
        teams_dir = os.path.join(self.ENV_DIR, self.TEAMS_SUBDIR)
        if os.path.exists(teams_dir):
            shutil.rmtree(teams_dir)
        os.makedirs(teams_dir)
        self.teams = []
        for team_zip in os.listdir(teams_root):
            if team_zip.endswith(".zip"):
                team_zip_path = os.path.join(teams_root, team_zip)
                self._setup_team(team_zip_path, teams_dir, add_ff_binary=True)

        # Add the staff team, if necessary
        if include_staff_team:
            if not os.path.exists(self.STAFF_TEAM_ZIP_FILE):
                logging.error('File %s could not be found. Aborting.' % self.STAFF_TEAM_ZIP_FILE)
                sys.exit(1)
            self._setup_team(self.STAFF_TEAM_ZIP_FILE, teams_dir, add_ff_binary=True)

        self.ladder = {n: [] for n, _ in self.teams}
        self.games = []
        self.errors = {n: 0 for n, _ in self.teams}
        self.team_stats = {n: 0 for n, _ in self.teams}



    def _close(self):
        self.run.do_close()


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

        shutil.rmtree(self.RESULTS_DIR)
        shutil.rmtree(self.ENV_DIR)


    def _generate_main_html(self):
        """
        Generates the html that points at the html files of all the runs.
        The html is saved in www/results.html.
        """
        # regenerate main html
        main_html = "<html><body><h1>Results Pacman %s Tournament by Date</h1>" % self.organizer
        for root, dirs, files in os.walk(self.www_path):
            for d in dirs:
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
                if line.find("Blue agent crashed") != -1:
                    self.errors[blue_team_name] += 1
        return score, winner, loser, bug
    
    
    def _generate_output(self):
        """
        Generates the output HTML of the report of the tournament and returns it.
        """
        output = "<html><body><h1>Date Tournament %s </h1><br><table border=\"1\">" % self.contest_run_id
        output += "<tr><th>Team</th><th>Points</th><th>Win</th><th>Tie</th><th>Lost</th><th>FAILED</th><th>Score Balance</th></tr>"
        for key, (points, wins, draws, loses, errors, sum_score) in sorted(self.team_stats.items(), key=lambda (k, v): v[0], reverse=True):
            output += "<tr><td align=\"center\">%s</td><td align=\"center\">%d</td><td align=\"center\">%d</td><td align=\"center\" >%d</td><td align=\"center\">%d</td><td align=\"center\" >%d</td><td align=\"center\" >%d</td></tr>" % (
            key, points, wins, draws, loses, errors, sum_score)
        output += "</table>"
    
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
    
    
    def _prepare_platform(self, contest_zip_file_path, layouts_zip_file_path, destination):
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
        self.layouts = [file_in_zip[:-4] for file_in_zip in layouts_zip_file.namelist()]
    

    def _setup_team(self, zip_file, destination, add_ff_binary=True):
        """
        Extracts team.py from the team zip file into a directory named after the zip file inside the given destination.
        Information on the teams are saved in the member variable teams.
        
        :param zip_file: the zip file of the team.
        :param destination: the directory where the team directory is to be created.
        :param add_ff_binary: whether to add the ff binary to the team folder.
        :raises KeyError if the zip file contains multiple copies of team.py, non of which is in the root.
        """
        student_zip_file = zipfile.ZipFile(zip_file)
        team_name = os.path.basename(zip_file)[:-4]
        team_destination_dir = os.path.join(destination, team_name)
        desired_file = 'team.py'
        student_zip_file.extractall(team_destination_dir)
    
        if add_ff_binary:
            shutil.copy('ff', team_destination_dir)
    
        agent_factory = os.path.join(self.TEAMS_SUBDIR, team_name, desired_file)
        self.teams.append((team_name, agent_factory))
    
    
    def _run_match(self, red_team, blue_team, layout):

        (red_team_name, red_team_agent_factory) = red_team
        (blue_team_name, blue_team_agent_factory) = blue_team
        print('Running game %s vs %s (layout: %s).' % (red_team_name, blue_team_name, layout), end='')
        sys.stdout.flush()

        command = 'python capture.py -r {red_team_agent_factory} -b {blue_team_agent_factory} -l {layout} -i {steps} -q --record'.format(
                red_team_agent_factory=red_team_agent_factory, blue_team_agent_factory=blue_team_agent_factory,
                layout=layout, steps=self.MAX_STEPS)
        logging.info(command)
        exit_code, output = commands.getstatusoutput('cd %s && %s' % (self.ENV_DIR, command))

        log_file_name = '{red_team_name}_vs_{blue_team_name}_{layout}.log'.format(
            layout=layout, run_id=self.contest_run_id, red_team_name=red_team_name, blue_team_name=blue_team_name)
        # results/results_<run_id>/{red_team_name}_vs_{blue_team_name}_{layout}.log
        with open(os.path.join(self.results_dir_full_path, log_file_name), 'w') as f:
            print(output, file=f)

        if exit_code == 0:
            print(' Successful. Output in {output_file}.'.format(output_file=log_file_name))
        else:
            print(' Failed. Output in {output_file}.'.format(output_file=log_file_name))
    
        score, winner, loser, bug = self._parse_result(output, red_team_name, blue_team_name)


        if not bug:
            if winner is None:
                self.ladder[red_team_name].append(score)
                self.ladder[blue_team_name].append(score)
            else:
                self.ladder[winner].append(score)
                self.ladder[loser].append(-score)

        replay_file_name = '{red_team_name}_vs_{blue_team_name}_{layout}.replay'.format(
            layout=layout, run_id=self.contest_run_id, red_team_name=red_team_name, blue_team_name=blue_team_name)

        replays = glob.glob('replay*')
        if replays:
            shutil.move(os.path.join(self.ENV_DIR, replays[0]),
        # results/results_<run_id>/{red_team_name}_vs_{blue_team_name}_{layout}.replay
                    os.path.join(self.results_dir_full_path, replay_file_name))
        if not bug:
            self.games.append((red_team_name, blue_team_name, layout, score, winner))
        else:
            self.games.append((red_team_name, blue_team_name, layout, 9999, winner))


    def run_contest(self):

        os.makedirs(self.results_dir_full_path)

        if len(self.teams) <= 1:
            output = "<html><body><h1>Date Tournament %s <br> 0 Teams participated!!</h1>" % self.contest_run_id
            output += "</body></html>"
            with open("results_%s/results.html" % self.contest_run_id, "w") as f:
                print(output, file=f)

        for red_team, blue_team in combinations(self.teams, r=2):
            for layout in self.layouts:
                self._run_match(red_team, blue_team, layout)


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


if __name__ == '__main__':
    settings = load_settings()
    runner = ContestRunner(**settings)
    runner.run_contest()
    runner.update_www()
