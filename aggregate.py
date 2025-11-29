#!/usr/bin/env python3
"""Parse downloaded XML files and create combined all.json"""

import json
from datetime import datetime
from lxml import etree

def norm(s):
    return " ".join(str(s).strip().split()).lower() if s else None

def clean(s):
    return " ".join(str(s).strip().split()) if s and str(s).strip() else None

def parse_date(s):
    if not s: return None
    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y']:
        try: return datetime.strptime(s.strip(), fmt).strftime('%Y-%m-%d')
        except: pass
    return s.strip() if len(s.strip()) >= 4 else None

def parse_eu(content):
    entities = []
    root = etree.fromstring(content)
    for entity in root.iter():
        if 'sanctionEntity' not in entity.tag: continue
        rec = {'name': None, 'type': 'unknown', 'aliases': [], 'birthdates': [], 'identifiers': [], 'source': ['EU'], 'source_id': entity.get('logicalId')}
        for na in entity.iter():
            if 'nameAlias' in na.tag:
                name = clean(na.get('wholeName'))
                if name:
                    if not rec['name']: rec['name'] = name
                    elif name not in rec['aliases']: rec['aliases'].append(name)
        for st in entity.iter():
            if 'subjectType' in st.tag:
                code = st.get('code') or st.text or ''
                rec['type'] = 'individual' if 'person' in code.lower() else 'entity'
        for bd in entity.iter():
            if 'birthdate' in bd.tag.lower():
                d = parse_date(bd.get('birthdate') or bd.text)
                if d and d not in rec['birthdates']: rec['birthdates'].append(d)
        for ident in entity.iter():
            if 'identification' in ident.tag.lower() and ident.get('number'):
                rec['identifiers'].append({'type': ident.get('identificationTypeCode'), 'number': ident.get('number'), 'country': ident.get('countryIso2Code')})
        if rec['name']: entities.append(rec)
    return entities

def parse_un(content):
    entities = []
    root = etree.fromstring(content)
    for tag, etype in [('INDIVIDUAL', 'individual'), ('ENTITY', 'entity')]:
        for elem in root.iter(tag):
            rec = {'name': None, 'type': etype, 'aliases': [], 'birthdates': [], 'identifiers': [], 'source': ['UN'], 'source_id': elem.findtext('REFERENCE_NUMBER')}
            if etype == 'individual':
                parts = [elem.findtext(f) for f in ['FIRST_NAME', 'SECOND_NAME', 'THIRD_NAME', 'FOURTH_NAME'] if elem.findtext(f)]
                rec['name'] = clean(' '.join(parts))
            else:
                rec['name'] = clean(elem.findtext('FIRST_NAME'))
            for alias in elem.iter('INDIVIDUAL_ALIAS'):
                a = clean(alias.findtext('ALIAS_NAME'))
                if a and a != rec['name'] and a not in rec['aliases']: rec['aliases'].append(a)
            for alias in elem.iter('ENTITY_ALIAS'):
                a = clean(alias.findtext('ALIAS_NAME'))
                if a and a != rec['name'] and a not in rec['aliases']: rec['aliases'].append(a)
            for dob in elem.iter('INDIVIDUAL_DATE_OF_BIRTH'):
                d = parse_date(dob.findtext('DATE') or dob.findtext('YEAR'))
                if d and d not in rec['birthdates']: rec['birthdates'].append(d)
            for doc in elem.iter('INDIVIDUAL_DOCUMENT'):
                if doc.findtext('NUMBER'):
                    rec['identifiers'].append({'type': doc.findtext('TYPE_OF_DOCUMENT'), 'number': doc.findtext('NUMBER'), 'country': doc.findtext('ISSUING_COUNTRY')})
            if rec['name']: entities.append(rec)
    return entities

def parse_ofac(content):
    entities = []
    root = etree.fromstring(content)
    for entry in root.iter():
        if not (entry.tag.endswith('sdnEntry') or entry.tag == 'sdnEntry'): continue
        rec = {'name': None, 'type': 'unknown', 'aliases': [], 'birthdates': [], 'identifiers': [], 'source': ['OFAC'], 'source_id': None}
        for e in entry.iter():
            if e.tag.endswith('uid') or e.tag == 'uid': rec['source_id'] = e.text; break
        for e in entry.iter():
            if e.tag.endswith('sdnType') or e.tag == 'sdnType':
                rec['type'] = 'individual' if 'individual' in (e.text or '').lower() else 'entity'; break
        parts = []
        for field in ['firstName', 'lastName']:
            for e in entry.iter():
                if e.tag.endswith(field) and e.text: parts.append(e.text.strip()); break
        rec['name'] = clean(' '.join(parts)) if parts else None
        if not rec['name']:
            for e in entry.iter():
                if e.tag.endswith('lastName') and e.text: rec['name'] = clean(e.text); break
        for aka in entry.iter():
            if 'aka' in aka.tag.lower() and 'list' not in aka.tag.lower():
                ap = []
                for f in ['firstName', 'lastName']:
                    for sub in aka.iter():
                        if sub.tag.endswith(f) and sub.text: ap.append(sub.text.strip()); break
                a = clean(' '.join(ap))
                if a and a != rec['name'] and a not in rec['aliases']: rec['aliases'].append(a)
        for dob in entry.iter():
            if 'dateOfBirth' in dob.tag and 'List' not in dob.tag:
                for sub in dob.iter():
                    if sub.text:
                        d = parse_date(sub.text)
                        if d and d not in rec['birthdates']: rec['birthdates'].append(d)
        for ide in entry.iter():
            if 'id' in ide.tag.lower() and 'List' not in ide.tag:
                id_type = id_num = id_country = None
                for sub in ide.iter():
                    if 'idType' in sub.tag: id_type = sub.text
                    elif 'idNumber' in sub.tag: id_num = sub.text
                    elif 'idCountry' in sub.tag: id_country = sub.text
                if id_num: rec['identifiers'].append({'type': id_type, 'number': id_num, 'country': id_country})
        if rec['name']: entities.append(rec)
    return entities

def parse_uk(content):
    entities = []
    root = etree.fromstring(content)
    for desig in root.iter():
        if 'Designation' not in desig.tag: continue
        rec = {'name': None, 'type': 'unknown', 'aliases': [], 'birthdates': [], 'identifiers': [], 'source': ['UK'], 'source_id': None}
        for e in desig.iter():
            if 'UniqueID' in e.tag: rec['source_id'] = e.text; break
        for ind in desig.iter():
            if 'Individual' in ind.tag and 'Name' not in ind.tag:
                rec['type'] = 'individual'
                for ne in ind.iter():
                    if 'Name' in ne.tag:
                        parts = [sub.text.strip() for sub in ne.iter() if sub.text and any(f'Name{i}' in sub.tag for i in range(1,7))]
                        rec['name'] = clean(' '.join(parts)); break
                for dob in ind.iter():
                    if 'DOB' in dob.tag and dob.text:
                        d = parse_date(dob.text)
                        if d and d not in rec['birthdates']: rec['birthdates'].append(d)
                for pp in ind.iter():
                    if 'PassportNumber' in pp.tag and pp.text:
                        rec['identifiers'].append({'type': 'Passport', 'number': pp.text})
                break
        if rec['type'] == 'unknown':
            for ent in desig.iter():
                if 'Entity' in ent.tag and 'Name' not in ent.tag:
                    rec['type'] = 'entity'
                    for ne in ent.iter():
                        if 'Name' in ne.tag:
                            for sub in ne.iter():
                                if sub.text: rec['name'] = clean(sub.text); break
                            break
                    break
        for alias in desig.iter():
            if 'Alias' in alias.tag:
                parts = [sub.text.strip() for sub in alias.iter() if sub.text and any(f'Name{i}' in sub.tag for i in range(1,7))]
                a = clean(' '.join(parts))
                if a and a != rec['name'] and a not in rec['aliases']: rec['aliases'].append(a)
        if rec['name']: entities.append(rec)
    return entities

def merge(datasets):
    merged, index = [], {}
    for ds in datasets:
        for e in ds:
            key = norm(e.get('name'))
            if not key: continue
            if key not in index:
                index[key] = len(merged)
                merged.append(e)
            else:
                ex = merged[index[key]]
                for s in e.get('source', []):
                    if s not in ex['source']: ex['source'].append(s)
                for a in e.get('aliases', []):
                    if a not in ex['aliases']: ex['aliases'].append(a)
                for b in e.get('birthdates', []):
                    if b not in ex['birthdates']: ex['birthdates'].append(b)
                nums = {i['number'] for i in ex.get('identifiers', [])}
                for i in e.get('identifiers', []):
                    if i['number'] not in nums: ex['identifiers'].append(i)
                if ex['type'] == 'unknown' and e['type'] != 'unknown': ex['type'] = e['type']
    return merged

def main():
    results = {}
    for name, parser in [('EU', parse_eu), ('UN', parse_un), ('OFAC', parse_ofac), ('UK', parse_uk)]:
        try:
            with open(f'data/{name.lower()}.xml', 'rb') as f:
                results[name] = parser(f.read())
            print(f"{name}: {len(results[name])} entities")
        except Exception as e:
            print(f"{name}: ERROR - {e}")
            results[name] = []
    
    merged = merge(list(results.values()))
    by_source = {}
    for e in merged:
        for s in e['source']: by_source[s] = by_source.get(s, 0) + 1
    
    output = {
        'metadata': {
            'generated': datetime.utcnow().isoformat() + 'Z',
            'total': len(merged),
            'by_source': by_source
        },
        'entities': merged
    }
    
    with open('data/all.json', 'w') as f:
        json.dump(output, f, separators=(',', ':'))
    
    print(f"Combined: {len(merged)} entities -> data/all.json")

if __name__ == '__main__':
    main()
