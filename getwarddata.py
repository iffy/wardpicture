import requests
import getpass
import re
import os
import json
import sys
import time
import argparse
from ordereddict import OrderedDict
import math
from collections import defaultdict
from filepath import FilePath
from jinja2 import Environment, FileSystemLoader


class ValueGetters(object):

    def __init__(self):
        self.funcs = {}
        self.raws = {}

    def __get__(self, key, obj):
        return self

    def raw(self, name):
        def deco(f):
            self.raws[name] = f
            return f
        return deco


    def value(self, name):
        def deco(f):
            self.funcs[name] = f
            return f
        return deco


def xAtATime(it, x):
    """
    Yield up to x elements at a time from the iterator.
    """
    while True:
        ret = []
        for i in xrange(x):
            try:
                v = it.next()
            except StopIteration:
                yield ret
                return
            ret.append(v)
        yield ret


class LDSClient(object):

    vals = ValueGetters()

    def __init__(self, root, username, password):
        self._session = None
        self.username = username
        self.password = password
        self.root = FilePath(root)
        self.raw_root = self.root.child('raw')
        if not self.root.exists():
            self.root.makedirs()
        if not self.raw_root.exists():
            self.raw_root.makedirs()
        self.photo_root = self.root.child('photos')
        if not self.photo_root.exists():
            self.photo_root.makedirs()

    def assertOk(self, response):
        if not response.ok:
            sys.stderr.write('not okay: %r\n' % (response,))
            sys.stderr.write(repr(response.text)[:200] + '\n')
            raise Exception('response not okay', response)

    def log(self, message):
        sys.stderr.write(message + '\n')

    def authenticate(self):
        if self._session:
            return self._session
        self.log('Signing in...')
        s = self._session = requests.session()
        r = s.get('https://ident.lds.org/sso/UI/Login')
        r = s.post('https://ident.lds.org/sso/UI/Login', params={
            'IDToken1': self.username,
            'IDToken2': self.password,
            'IDButton': 'Log In',
        })
        self.assertOk(r)
        return self._session

    def storeRawValue(self, filename, value):
        self.raw_root.child(filename).setContent(json.dumps(value))

    def hasRawValue(self, filename):
        fp = self.raw_root.child(filename)
        if fp.exists():
            return fp
        return None

    def getRawValue(self, filename, default_value=None):
        fp = self.hasRawValue(filename)
        if fp:
            return json.loads(fp.getContent())
        else:
            return default_value

    def updateRawData(self):
        for name, func in self.vals.raws.items():
            if self.hasRawValue(name):
                # already has a value; do not recompute
                self.log('[%s] data already present' % (name,))
                continue
            self.log('[%s] fetching...' % (name,))
            data = func(self)
            self.storeRawValue(name, data)

    @vals.raw('unit_number')
    def get_unitNumber(self):
        s = self.authenticate()
        r = s.get('https://www.lds.org/mls/mbr/records/member-list?lang=eng')
        self.assertOk(r)

        # this is probably pretty fragile...
        re_unit_number = re.compile(r"window.unitNumber\s=\s'(.*?)';")
        m = re_unit_number.search(r.text)
        return m.groups()[0]

    @vals.raw('member_list')
    def get_memberList(self):
        s = self.authenticate()
        unit_number = self.getRawValue('unit_number')
        r = s.get('https://www.lds.org/mls/mbr/services/report/member-list', params={
            'lang': 'eng',
            'unitNumber': unit_number,
        })
        self.assertOk(r)
        return r.json()

    @vals.raw('members_with_callings')
    def get_membersWithCallings(self):
        s = self.authenticate()
        unit_number = self.getRawValue('unit_number')
        r = s.get('https://www.lds.org/mls/mbr/services/report/members-with-callings', params={
            'lang': 'eng',
            'unitNumber': unit_number,
        }, headers={
            'Accept': 'application/json',
        })
        self.assertOk(r)
        return r.json()

    @vals.raw('members_without_callings')
    def get_membersWithoutCallings(self):
        s = self.authenticate()
        unit_number = self.getRawValue('unit_number')
        r = s.get('https://www.lds.org/mls/mbr/services/orgs/members-without-callings', params={
            'lang': 'eng',
            'unitNumber': unit_number,
        }, headers={
            'Accept': 'application/json',
        })
        self.assertOk(r)
        return r.json()

    #----------------------------
    # photos
    #----------------------------

    def _memberPhotoFilePath(self, member_id, size='large', ext='jpg'):
        """
        Valid size options are:
            - large
            - medium
            - original
            - thumbnail
        """
        return self.photo_root.child('solo-%s-%s.%s' % (member_id, size, ext))

    def _memberIDsWithNoPhoto(self, size='large'):
        members = self.getRawValue('member_list')
        for member in members:
            member_id = member['id']
            photo_fp = self._memberPhotoFilePath(member_id, size)
            if photo_fp.exists():
                continue
            yield member_id

    def updatePhotos(self, size='large'):
        s = self.authenticate()
        self.log('Getting photos...')
        for member_ids in xAtATime(self._memberIDsWithNoPhoto(size), 19):
            if not member_ids:
                continue
            try:
                r = s.get('https://www.lds.org/directory/services/ludrs/photo/url/'+','.join(map(str, member_ids))+'/individual')
                data = r.json()
            except ValueError:
                print 'Error on', member_ids
                raise
            for member_id, result in zip(member_ids, data):
                fp = self._memberPhotoFilePath(member_id, size)
                uri = result[size + 'Uri']
                if uri:
                    print 'fetching photo for', member_id
                    uri = 'https://www.lds.org' + uri
                    image_data = s.get(uri)
                    content_type = image_data.headers['content-type']
                    if content_type != 'image/jpeg':
                        print 'NON-JPEG: ', content_type, member_id
                        continue
                    fp.setContent(image_data.content)
                else:
                    print 'no photo for', member_id
            time.sleep(0.5)



abbreviations = [
    ('Relief Society', 'RS'),
    ('Visiting Teaching', 'VT'),
    ('Young Women', 'YW'),
    ('Young Men', 'YM'),
    ('Elders Quorum', 'EQ'),
    ('High Priests Group', 'HP'),
    ('High Priests', 'HP'),
    ('Home Teaching', 'HT'),
    (' Member', ''),
    ('First', '1st'),
    ('Second', '2nd'),
    ('Secretary', 'Sec.'),
    ('Sunday School', 'SS'),
    ('President', 'Pres.'),
    ('Supervisor', 'Sup.'),

    # IDs
    ('AARONIC_PRIESTHOOD_QUORUM_ADVISERS', 'Aaronic Advisers'),
    ('ACTIVITIES_AND_SPORTS_YOUNG_WOMEN', 'YW Act, & Sports'),
    ('ACTIVITY_DAYS', 'Activity Days'),
    ('BEAR_DEN', 'Bears'),
    ('BEEHIVE_PRESIDENCY', 'Beehive Presidency'),
    ('BISHOPRIC', 'Bishopric'),
    ('BOY_SCOUTS', 'Scouts'),
    ('CUB_SCOUTS', 'Cubs'),
    ('COMPASSIONATE_SERVICE', 'Compassionate Service'),
    ('COURSE', 'Course'),
    ('DEACONS_QUORUM_PRESIDENCY', 'Decons Presidency'),
    ('ELDERS_QUORUM_PRESIDENCY', 'EQ Presidency'),
    ('ELEVEN_YEAR_OLD_SCOUTS', '11-year-old Scouts'),
    ('EMPLOYMENT_AND_WELFARE_STAKE', 'Stake Employment and Welfare'),
    ('EMPLOYMENT_AND_WELFARE_WARD_BRANCH', 'Ward Employment and Welfare'),
    ('FACILITIES_WARD_BRANCH', 'Ward Facilities'),
    ('TEMPLE_AND_FAMILY_HISTORY', 'Temple and Family History'),
    ('FAMILY_HISTORY', 'Family History'),
    ('GOSPEL_DOCTRINE', 'Gospel Doctrine'),
    ('HIGH_COUNCIL', 'High Council'),
    ('HOME_TEACHING_DISTRICT_SUPERVISORS_ELDERS_QUORUM', 'EQ HT'),
    ('HOME_TEACHING_DISTRICT_SUPERVISORS_HIGH_PRIESTS_GROUP', 'HP HT'),
    ('INSTRUCTORS_ELDERS_QUORUM', 'EQ Instructors'),
    ('INSTRUCTORS_HIGH_PRIESTS_GROUP', 'HP Instructors'),
    ('HIGH_PRIESTS_GROUP_LEADERSHIP', 'HP Leadership'),
    ('HIGH_PRIESTS_GROUP', 'HP'),
    ('LAUREL_PRESIDENCY', 'Laurel Presidency'),
    ('LIBRARY', 'Library'),
    ('MEETINGS', 'Meetings'),
    ('MIA_MAID_PRESIDENCY', 'Mia Maid Presidency'),
    ('MISSIONARY_PREPARATION', 'Mission Prep'),
    ('MUSIC_PRIMARY', 'Primary Music'),
    ('MUSIC_RELIEF_SOCIETY', 'RS Music'),
    ('MUSIC_STAKE', 'Stake Music'),
    ('MUSIC_WARD_BRANCH', 'Ward Music'),
    ('NURSERY', 'Nursery'),
    ('OTHER_CALLINGS', 'Other'),
    ('PRIESTS_QUORUM_PRESIDENCY', 'Priests Presidency'),
    ('PRIMARY_PRESIDENCY', 'Primary Presidency'),
    ('STAKE_PRIMARY_PRESIDENCY', 'Stake Primary'),
    ('PRIMARY', 'Primary'),
    ('RELIEF_SOCIETY_PRESIDENCY', 'RS Presidency'),
    ('RELIEF_SOCIETY', 'RS'),
    ('STAKE_YOUNG_MEN_PRESIDENCY', 'Stake YM'),
    ('STAKE_YOUNG_WOMEN_PRESIDENCY', 'Stake YW'),
    ('SUNBEAM', 'Sunbeam'),
    ('UNASSIGNED_TEACHERS_SUNDAY_SCHOOL', 'Unassigned Teachers'),
    ('SUNDAY_SCHOOL_PRESIDENCY', 'SS Presidency'),
    ('SUNDAY_SCHOOL', 'Sunday School'),
    ('TEACHERS_QUORUM_PRESIDENCY', 'Teachers Presidency'),
    ('TEACHERS', 'Teachers'),
    ('TECHNOLOGY_WARD_BRANCH', 'Ward Tech'),
    ('VALIANT_10', 'Valiant 10'),
    ('VALIANT_11', 'Valiant 11'),
    ('VALIANT_8', 'Valiant 8'),
    ('VALIANT_9', 'Valiant 9'),
    ('VARSITY', 'Varsity'),
    ('VENTURING', 'Venturing'),
    ('VISITING_TEACHING', 'VT'),
    ('WARD_MISSIONARIES', 'Ward Missionaries'),
    ('WEBELOS_DEN', 'Webelos'),
    ('WOLF_DEN', 'Wolf'),
    ('YOUNG_MEN_PRESIDENCY', 'YM Presidency'),
    ('YOUNG_SINGLE_ADULT_WARD_BRANCH', 'YSA'),
    ('YOUNG_WOMEN_CLASS_ADVISERS', 'YW Class Advisers'),
    ('YOUNG_WOMEN_PRESIDENCY', 'YW Presidency'),
    ('YOUNG_WOMEN', 'YW'),
    ('_', ' '),
]

prefOrder = '''
# Primary
PRIMARY_PRESIDENCY
PRIMARY
NURSERY
SUNBEAM
CTR_4
CTR_5
CTR_6
CTR_7
VALIANT_8
VALIANT_9
VALIANT_10
VALIANT_11
COURSE_13
COURSE_15
UNASSIGNED_TEACHERS_PRIMARY

# YW
YOUNG_WOMEN_PRESIDENCY
YOUNG_WOMEN_CLASS_ADVISERS
YOUNG_WOMEN
BEEHIVE_PRESIDENCY
MIA_MAID_PRESIDENCY
LAUREL_PRESIDENCY
ACTIVITIES_AND_SPORTS_YOUNG_WOMEN

# YM
YOUNG_MEN_PRESIDENCY
AARONIC_PRIESTHOOD_QUORUM_ADVISERS
DEACONS_QUORUM_PRESIDENCY
TEACHERS_QUORUM_PRESIDENCY
PRIESTS_QUORUM_PRESIDENCY

BOY_SCOUTS
ELEVEN_YEAR_OLD_SCOUTS
VARSITY
VENTURING
ACTIVITIES_AND_SPORTS_YOUNG_MEN

# Cubs
ACTIVITY_DAYS
CUB_SCOUTS
BEAR_DEN
WOLF_DEN
WEBELOS_DEN
BISHOPRIC

# RS
RELIEF_SOCIETY_PRESIDENCY
RELIEF_SOCIETY
VISITING_TEACHING
MUSIC_RELIEF_SOCIETY
TEACHERS
MEETINGS
COMPASSIONATE_SERVICE

# HP
HIGH_PRIESTS_GROUP_LEADERSHIP
HIGH_PRIESTS_GROUP
HOME_TEACHING_DISTRICT_SUPERVISORS_HIGH_PRIESTS_GROUP
INSTRUCTORS_HIGH_PRIESTS_GROUP

ELDERS_QUORUM_PRESIDENCY
HOME_TEACHING_DISTRICT_SUPERVISORS_ELDERS_QUORUM
INSTRUCTORS_ELDERS_QUORUM

# Missionary
WARD_MISSIONARIES
MISSIONARY_PREPARATION
Full-Time Missionaries

FAMILY_HISTORY
TEMPLE_AND_FAMILY_HISTORY

# Sunday School
SUNDAY_SCHOOL_PRESIDENCY
SUNDAY_SCHOOL
GOSPEL_DOCTRINE

UNASSIGNED_TEACHERS_SUNDAY_SCHOOL
LIBRARY

HIGH_COUNCIL
STAKE_YOUNG_MEN_PRESIDENCY
STAKE_YOUNG_WOMEN_PRESIDENCY
STAKE_PRIMARY_PRESIDENCY
MUSIC_STAKE

# Other
MUSIC_WARD_BRANCH
MUSIC_PRIMARY

OTHER_CALLINGS
TECHNOLOGY_WARD_BRANCH
YOUNG_SINGLE_ADULT_WARD_BRANCH
EMPLOYMENT_AND_WELFARE_STAKE
EMPLOYMENT_AND_WELFARE_WARD_BRANCH
FACILITIES_WARD_BRANCH
OTHER
'''.split('\n')

linebreak_before = [
'DEACONS_QUORUM_PRESIDENCY',
'BISHOPRIC',
]


def sortbyPref(pref):
    def getIndex(a):
        try:
            return pref.index(a)
        except:
            return len(pref)
    def compare(x, y):
        xi = getIndex(x)
        yi = getIndex(y)
        if xi == yi:
            # alphabetical
            return cmp(x, y)
        else:
            return cmp(xi, yi)
    return compare

def abbreviateCalling(x):
    if x is None:
        return x
    for l, s in abbreviations:
        x = x.replace(l, s)
    return x

def mapCallings(client, data_dir='data', template_root='templates'):
    data_fp = FilePath(data_dir)
    output_root = data_fp.child('output')
    if not output_root.exists():
        output_root.makedirs()
    jenv = Environment(loader=FileSystemLoader(template_root))
    jenv.filters['abbr'] = abbreviateCalling
    jenv.globals['math'] = math
    template = jenv.get_template('callingmap.html')
    
    #members = client.getRawValue('member_list')
    callings = client.getRawValue('members_with_callings')
    no_calling = client.getRawValue('members_without_callings')
    no_calling = [x for x in no_calling if x['age'] >= 12]

    # get the groups and subgroups organized into dicts
    groups = OrderedDict()
    by_suborg = {}
    for line in prefOrder:
        if line.startswith('#'):
            # heading
            groups[line[1:].strip()] = OrderedDict()
        elif line.strip():
            subgroup_key = line.strip()
            groups[groups.keys()[-1]][subgroup_key] = by_suborg[subgroup_key] = []
    
    # put each calling into the right subgroup
    # also count the number of callings per person
    calling_counts = defaultdict(lambda:0)
    for calling in callings:
        suborg = calling['subOrgType'] or calling['organization']
        by_suborg[suborg].append(calling)
        calling_counts[calling['id']] += 1

    fp = output_root.child('callingmap.html')
    fp.setContent(template.render(
        orgs=groups,
        calling_counts=calling_counts,
        no_calling=no_calling).encode('utf-8'))
    print 'wrote', fp.path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('--update-photos', '-p', dest='update_photos', action='store_true')
    parser.add_argument('--template-dir', '-t', dest='template_dir', default='templates')
    parser.add_argument('--username', '-u', dest='username')
    parser.add_argument('--no-connect', '-n', dest='connect', action='store_false')

    parser.add_argument('data_dir', nargs='?', default='data')
    parser.set_defaults(username=os.environ.get('LDS_USERNAME', None))
    parser.set_defaults(password=os.environ.get('LDS_PASSWORD', None))

    args = parser.parse_args()

    if args.connect:
        if not args.username:
            args.username = raw_input('(blank LDS_USERNAME) Username: ')
        if not args.password:
            args.password = getpass.getpass('(blank LDS_PASSWORD) Password: ')

    client = LDSClient(args.data_dir, args.username, args.password)

    if args.connect:
        client.updateRawData()
        if args.update_photos:
            client.updatePhotos()
        else:
            print 'skipping photo update (pass --update-photos if you want to update them)'
    else:
        print 'skipping connection to lds.org'

    mapCallings(client, args.data_dir, args.template_dir)

