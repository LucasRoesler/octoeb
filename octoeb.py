#!/usr/bin/env python
"""
Author: Lucas Roesler <lucas@eventboard.io>

OctoEB is a script to help with the creation of GitHub releases for Eventboard
projects.  This is to help us avoid merge, branch, and tag issues. It also
simplifies the process so that it is executed the same way by each developer
each time.

## Installation
The only external library that this tool depends on is Requests.  Clone the
repo run

    pip install -r requirements

Run

    ./install.sh

To verify the install, start a new shell and run

    octoeb -h

## Configuration
The script looks for the file `.octoebrc` in either
your home directory or the current directory.  We expect this file to
contain the following ini-style configuration:

```
[repo]
OWNER=repo-owner
REPO=repo-name
TOKEN=oauth-token
USER=email@test.com
```

1) OWNER and REPO are https://github.com/OWNER/REPO when you vist a repo on
   GitHub, so for example https://github.com/enderlabs/eventboard.io gives
   OWNER=enderlabs and REPO=eventboard.io
2) The token can be obtained from https://github.com/settings/tokens
3) USER is your login email for GitHub


## Usage
There are three major command `start`, `qa`, and `release`. Enter
    $ octoeb start -h
    $ octoeb qa -h
    $ octoeb release -h
respectively for usage details.
"""

import argparse
import ConfigParser
import logging
import re
import sys

import requests


logger = logging.getLogger(__name__)
desc = """\
Eventboard releases script\
"""


def get_config():
    config = ConfigParser.ConfigParser()
    config.read(['~/.octoebrc', '.octoebrc'])
    return (
        config.get('repo', 'USER'),
        config.get('repo', 'TOKEN'),
        config.get('repo', 'OWNER'),
        config.get('repo', 'REPO'),
    )


def get_numeric_level(level):
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: {}'.format(level))

    return numeric_level


def validate_version(version):
    if re.match(r'^(?:\.?\d+){4,5}$', version):
        return True

    raise Exception('Invalid version number {}'.format(version))


def extract_major_version(version):
    return '.'.join(version.split('.')[:4])


def validate_ticket_name(name):
    if re.match(r'^EB-\d+(?:-.+)?$', name):
        return True

    raise Exception('Invalid ticket name {}'.format(name))


class GitHubAPI(object):

    def __init__(self, user, token, owner, repo, *args, **kwargs):
        self.user = user
        self.token = token
        self.base = 'https://api.github.com/repos/{}/{}/'.format(owner, repo)

    def build_path(self, path):
        url = '{}{}'.format(self.base, path)

        logger.debug(url)
        return url

    def get(self, path):
        return requests.get(
            self.build_path(path),
            auth=(self.user, self.token)
        )

    def post(self, path, *args, **kwargs):
        logger.debug('GitHubAPI.post: {} {} {}'.format(path, args, kwargs))
        return requests.post(
            self.build_path(path),
            auth=(self.user, self.token), *args, **kwargs
        )

    def releases(self):
        return self.get('releases')

    def prereleases(self):
        resp = self.get('releases')

        logger.debug(resp)
        # raises error if none found
        resp.raise_for_status()

        try:
            releases = resp.json()
        except:
            logger.error('GitHubAPI.prereleases could not parse to json')
            releases = []

        return [x for x in releases if x.get('prerelease')]

    def latest_release(self):
        resp = self.get('releases/latest')

        logger.debug(resp)
        # raises error if none found
        resp.raise_for_status()

        return resp.json()

    def latest_prerelease(self):
        prereleases = self.prereleases()

        if not prereleases:
            raise Exception('No prereleases found')

        return prereleases[0]

    def get_release(self, name, raise_for_status=True):
        resp = self.get('releases/tags/{}'.format(name))

        logger.debug(resp)
        if raise_for_status:
            resp.raise_for_status()

        return resp.json()

    def get_branch(self, name, raise_for_status=True):
        resp = self.get('git/refs/heads/{}'.format(name))

        logger.debug(resp)
        if raise_for_status:
            resp.raise_for_status()

        return resp.json()

    def compare(self, base, head, raise_for_status=True):
        resp = self.get('compare/{}...{}'.format(base, head))

        logger.debug(resp)
        if raise_for_status:
            resp.raise_for_status()

        return resp.json()

    def create_branch(self, name, base_name):
        # raise an error if we can find the branch, continue if we get
        # a 404
        try:
            self.get_branch(name)
        except requests.exceptions.HTTPError:
            pass
        else:
            raise Exception(
                'Branch already started. Run'
                '\n\tgit fetch --all && get checkout {}'.format(name)
            )

        base = self.get_branch(base_name)
        try:
            branch_info = {
                'ref': 'refs/heads/{}'.format(name),
                'sha': base['object']['sha']
            }
        except KeyError:
            logger.error('base repsonse: {}'.format(base))
            raise Exception(
                'Could not locate the current SHA for '.format(base_name))

        resp = self.post('git/refs', json=branch_info)
        try:
            resp.raise_for_status()
        except:
            logger.error(resp.json())
            raise

        return resp.json()

    def create_release_branch(self, release_name):
        return self.create_branch(release_name, 'develop')

    def create_hotfix_branch(self, fix_name):
        return self.create_branch(fix_name, 'master')

    def create_pre_release(self, release_name):
        name = 'release-{}'.format(extract_major_version(release_name))
        release_branch = self.get_branch(name)

        try:
            self.get_release(release_name)
        except requests.exceptions.HTTPError:
            pass
        else:
            raise Exception(
                'Release already created.'
            )

        try:
            release_info = {
              "tag_name": release_name,
              "target_commitish": release_branch['object']['sha'],
              "name": 'release-{}'.format(release_name),
              "body": "",
              "draft": False,
              "prerelease": True
            }
        except KeyError:
            logger.error('Release branch repsonse: {}'.format(release_branch))
            raise Exception('Could not locate the current SHA for the release')

        resp = self.post('releases', json=release_info)
        try:
            resp.raise_for_status()
        except:
            logger.error(resp.json())
            raise

        return resp.json()

    def check_merge_status(self, release_name):
        """
        Verify that master is ahead of the latest release and for new releases
        we verify that the release branch has been merged.
        """
        release_base_branch = self.release_branch_name(release_name)
        prod = self.latest_release()
        prod_tag = prod.get('tag_name')
        if prod_tag is None:
            raise Exception('Production release tag not found!')

        c = self.compare(prod_tag, release_base_branch)
        status = c.get('status')

        if status in ['diverged', 'ahead']:
            raise Exception(
                'Release must be merged into master before being released')

        return True

    def release_branch_name(self, release_name):
        """
        Determine the base branch for the release.

        Hotfixes the base branch should be master. New releases should
        are based on a branch of the name `release-<major.version.number>`

        Returns:
            str
        """
        release_major_version = extract_major_version(release_name)

        try:
            prod = self.latest_release()
        except:
            prod = {}

        prod_major_version = extract_major_version(prod.get('tag_name', ''))

        if prod_major_version == release_major_version:
            return 'master'

        try:
            prod_next = self.latest_prerelease()
        except:
            prod_next = {}

        prod_next_major_version = extract_major_version(
            prod_next.get('tag_name', ''))

        if prod_next_major_version == release_major_version:
            return 'release-{}'.format(release_major_version)

    def create_release(self, release_name):

        # maybe we should just trying merging the branches
        # https://developer.github.com/v3/repos/merging/
        merge_status = self.compare(
            'master',
            'release-{}'.format(extract_major_version(release_name))
        ).get('status')

        # can be one of diverged, ahead, behind, identical according to
        # http://stackoverflow.com/a/23969867
        if merge_status in ['diverged', 'ahead']:
            raise Exception(
                'Release must be merged into master before release')

        try:
            self.get_release(release_name)
        except requests.exceptions.HTTPError:
            pass
        else:
            raise Exception(
                'Release already created.'
            )

        master = self.get_branch('master')
        try:
            release_info = {
              "tag_name": release_name,
              "target_commitish": master['object']['sha'],
              "name": 'release-{}'.format(release_name),
              "body": "",
              "draft": False,
              "prerelease": False
            }
        except KeyError:
            logger.error('Release branch repsonse: {}'.format(master))
            raise Exception('Could not locate the current SHA for the release')

        resp = self.post('releases', json=release_info)
        try:
            resp.raise_for_status()
        except:
            logger.error(resp.json())
            raise

        return resp.json()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument('--log', type=str, default='ERROR')
    subparsers = parser.add_subparsers()

    method_parser = subparsers.add_parser(
        'method', help='Directly call methods on the GitHubAPI wrapper.')
    method_parser.add_argument(
        'method_name', type=str, help='method to call on the GitHubAPI')
    method_parser.add_argument(
        'method_args', nargs='*', help='method arguments')

    start_parser = subparsers.add_parser(
        'start', help='Start a new release')
    start_subparsers = start_parser.add_subparsers()
    start_release_parser = start_subparsers.add_parser(
        'release', help='Start a new release')
    start_release_parser.add_argument(
        'start_ver', type=str, help='Version number to start')

    start_hotfix_parser = start_subparsers.add_parser(
        'hotfix', help='Start a new hotfix')
    start_hotfix_parser.add_argument(
        'start_fix', type=str, help='Ticket nuber of the bug')

    qa_parser = subparsers.add_parser(
        'qa', help='Create pre-release for qa')
    qa_parser.add_argument(
        'qa_ver', type=str, help='Version number to pre-release')

    release_parser = subparsers.add_parser(
        'release', help='Create release for production')
    release_parser.add_argument(
        'release_ver', type=str, help='Version number to release')

    # Get the CLI args
    args = parser.parse_args()

    # Handle setting the log level
    logging.basicConfig(level=get_numeric_level(args.log))

    # Setup the API
    api = GitHubAPI(*get_config())

    ######
    # Now handle branching based on the args
    ######

    # 1.directly call methods on the GitHubAPI
    if 'method_name' in args:
        print getattr(api, args.method_name)(*args.method_args)
        sys.exit()

    if 'start_ver' in args:
        try:
            validate_version(args.start_ver)
            name = 'release-{}'.format(extract_major_version(args.start_ver))
            branch = api.create_release_branch(name)
        except Exception as e:
            sys.exit(e.message)

        print 'Branch: {} created'.format(name)
        print branch.get('url')
        print '\tgit fetch --all && git checkout {}'.format(name)
        sys.exit()

    if 'start_fix' in args:
        try:
            validate_ticket_name(args.start_fix)
            name = 'hotfix-{}'.format(extract_major_version(args.start_fix))
            branch = api.create_hotfix_branch(name)
        except Exception as e:
            sys.exit(e.message)

        print 'Branch: {} created'.format(name)
        print branch.get('url')
        print '\tgit fetch --all && git checkout {}'.format(name)
        sys.exit()

    if 'qa_ver' in args:
        try:
            validate_version(args.qa_ver)
            api.create_pre_release(args.qa_ver)
            sys.exit()
        except Exception as e:
            sys.exit(e.message)

    if 'release_ver' in args:
        try:
            validate_version(args.release_ver)
            api.create_release(args.release_ver)
            sys.exit()
        except Exception as e:
            sys.exit(e.message)
