import requests
import getpass
import re
import os
import json
import sys
import time
import argparse
from filepath import FilePath


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
            r = s.get('https://www.lds.org/directory/services/ludrs/photo/url/'+','.join(map(str, member_ids))+'/individual')
            data = r.json()
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

if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('--update-photos', '-p', dest='update_photos', action='store_true')
    parser.add_argument('--username', '-u', dest='username')

    parser.add_argument('data_dir', nargs='?', default='data')

    args = parser.parse_args()

    args.username = args.username or os.environ.get('LDS_USERNAME', None)
    args.password = os.environ.get('LDS_PASSWORD', None)

    if not args.username:
        args.username = raw_input('Username: ')
    if not args.password:
        args.password = getpass.getpass('Password: ')

    client = LDSClient(args.data_dir, args.username, args.password)
    client.updateRawData()
    if args.update_photos:
        client.updatePhotos()
    else:
        print 'skipping photo update (pass --update-photos if you want to update them)'

