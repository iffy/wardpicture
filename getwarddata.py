import requests
import getpass
import re
import os
import json
import sys

big_data = {}

username = os.environ.get('LDS_USERNAME', None)
password = os.environ.get('LDS_PASSWORD', None)


if not username:
    username = raw_input('Username: ')
if not password:
    password = getpass.getpass('Password: ')


def assertOk(response):
    if not response.ok:
        sys.stderr.write('not okay: %r\n' % (response,))
        sys.stderr.write(repr(response.text)[:200] + '\n')
        raise Exception('response not okay', response)

s = requests.session()

#------------------------------------------------------------------------------
# login
#------------------------------------------------------------------------------
sys.stderr.write('signing in...\n')
r = s.get('https://ident.lds.org/sso/UI/Login')
r = s.post('https://ident.lds.org/sso/UI/Login', params={
    'IDToken1': username,
    'IDToken2': password,
    'IDButton': 'Log In',
})
assertOk(r)

#------------------------------------------------------------------------------
# get member list and unit number
#------------------------------------------------------------------------------
sys.stderr.write('looking for unit number...\n')
r = s.get('https://www.lds.org/mls/mbr/records/member-list?lang=eng')
assertOk(r)

# this is probably pretty fragile...
re_unit_number = re.compile(r"window.unitNumber\s=\s'(.*?)';")
m = re_unit_number.search(r.text)
unit_number = m.groups()[0]
big_data['unit_number'] = unit_number.strip()
sys.stderr.write('unit_number: {unit}\n'.format(unit=unit_number))

sys.stderr.write('getting member list...\n')
r = s.get('https://www.lds.org/mls/mbr/services/report/member-list', params={
    'lang': 'eng',
    'unitNumber': unit_number,
})
assertOk(r)
big_data['members'] = r.json()

sys.stderr.write('getting list of members with callings...\n')
r = s.get('https://www.lds.org/mls/mbr/services/report/members-with-callings', params={
    'lang': 'eng',
    'unitNumber': unit_number,
}, headers={
    'Accept': 'application/json',
})
assertOk(r)
big_data['callings'] = r.json()

sys.stderr.write('getting list of members without callings...\n')
r = s.get('https://www.lds.org/mls/mbr/services/orgs/members-without-callings', params={
    'lang': 'eng',
    'unitNumber': unit_number,
}, headers={
    'Accept': 'application/json',
})
assertOk(r)
big_data['no_callings'] = r.json()

print json.dumps(big_data)

