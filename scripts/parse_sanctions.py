#!/usr/bin/env python3
"""
Sanctions list parser — EU / UN / OFAC / UK (OFSI)
Produces a single normalized JSON with all available fields.
Merging: only when source UIDs match across lists (no fuzzy name merge).
"""

import json
import re
from datetime import datetime
from lxml import etree


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def clean(s):
    """Strip and collapse whitespace."""
    if not s:
        return None
    s = re.sub(r'\s+', ' ', str(s).strip())
    return s if s else None

def norm_name(s):
    """Lowercase + collapsed whitespace for comparison only."""
    return re.sub(r'\s+', ' ', str(s).strip().lower()) if s else None

def parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y', '%Y']:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    # partial dates like "circa 1960" — keep as-is if reasonably long
    return s if len(s) >= 4 else None

def local(tag):
    """Strip XML namespace from a tag."""
    return tag.split('}')[-1] if '}' in tag else tag

def find_text(elem, *tags):
    """Find first matching tag (namespace-agnostic) and return its text."""
    for tag in tags:
        for child in elem.iter():
            if local(child.tag) == tag and child.text and child.text.strip():
                return clean(child.text)
    return None

def find_all(elem, tag):
    """Return all child elements matching local tag name."""
    return [c for c in elem.iter() if local(c.tag) == tag]

def empty_record(source_name):
    return {
        'name': None,
        'type': 'unknown',
        'aliases': [],
        'birthdates': [],
        'birth_places': [],
        'nationalities': [],
        'addresses': [],
        'identifiers': [],
        'remarks': None,
        'programs': [],
        'listed_on': None,
        'source': [source_name],
        'source_ids': {},
    }


# ─────────────────────────────────────────────
# EU — Financial Sanctions Files (FSF)
# Tag: {http://...}sanctionEntity  (namespace varies)
# ─────────────────────────────────────────────

def parse_eu(content):
    entities = []
    root = etree.fromstring(content)

    # findall with wildcard namespace — avoids recursive iter() double-counting
    entity_tags = root.findall('.//{*}sanctionEntity')
    if not entity_tags:
        # fallback: try without namespace
        entity_tags = root.findall('.//sanctionEntity')

    for entity in entity_tags:
        rec = empty_record('EU')
        rec['source_ids']['EU'] = entity.get('logicalId') or entity.get('euReferenceNumber')

        # subject type
        for st in find_all(entity, 'subjectType'):
            code = (st.get('code') or st.text or '').lower()
            rec['type'] = 'individual' if 'person' in code else 'entity'

        # names — first nameAlias with strong quality is primary name
        for na in find_all(entity, 'nameAlias'):
            name = clean(na.get('wholeName'))
            if not name:
                continue
            if not rec['name']:
                rec['name'] = name
            elif name not in rec['aliases']:
                rec['aliases'].append(name)

        # birthdates + birth places
        for bd in find_all(entity, 'birthdate'):
            d = parse_date(bd.get('birthdate') or bd.get('year') or bd.text)
            if d and d not in rec['birthdates']:
                rec['birthdates'].append(d)
            place = clean(bd.get('city') or bd.get('place'))
            if place and place not in rec['birth_places']:
                rec['birth_places'].append(place)

        # nationalities
        for ci in find_all(entity, 'citizenship'):
            nat = clean(ci.get('countryIso2Code') or ci.get('countryDescription'))
            if nat and nat not in rec['nationalities']:
                rec['nationalities'].append(nat)

        # addresses
        for addr in find_all(entity, 'address'):
            parts = [
                addr.get('street'), addr.get('city'),
                addr.get('zipCode'), addr.get('countryDescription'),
                addr.get('countryIso2Code')
            ]
            full = clean(', '.join(p for p in parts if p))
            if full and full not in rec['addresses']:
                rec['addresses'].append(full)

        # identifiers (passports, IDs, etc.)
        for ident in find_all(entity, 'identification'):
            num = ident.get('number') or ident.get('identificationNumber')
            if num:
                rec['identifiers'].append({
                    'type': clean(ident.get('identificationTypeCode') or ident.get('identificationTypeDescription')),
                    'number': clean(num),
                    'country': clean(ident.get('countryIso2Code')),
                    'issued_by': clean(ident.get('issuedBy')),
                    'valid_from': parse_date(ident.get('validFrom')),
                    'valid_to': parse_date(ident.get('validTo')),
                })

        # regulation / listing date
        for reg in find_all(entity, 'regulation'):
            rec['listed_on'] = parse_date(reg.get('publicationDate') or reg.get('entryIntoForceDate'))
            break

        # remarks
        remarks_parts = []
        for r in find_all(entity, 'remark'):
            t = clean(r.text)
            if t:
                remarks_parts.append(t)
        rec['remarks'] = ' | '.join(remarks_parts) if remarks_parts else None

        if rec['name']:
            entities.append(rec)

    return entities


# ─────────────────────────────────────────────
# UN — Consolidated Sanctions List
# ─────────────────────────────────────────────

def parse_un(content):
    entities = []
    root = etree.fromstring(content)

    for tag, etype in [('INDIVIDUAL', 'individual'), ('ENTITY', 'entity')]:
        for elem in find_all(root, tag):
            rec = empty_record('UN')
            rec['type'] = etype
            rec['source_ids']['UN'] = find_text(elem, 'REFERENCE_NUMBER')

            # name
            if etype == 'individual':
                parts = [find_text(elem, f) for f in
                         ['FIRST_NAME','SECOND_NAME','THIRD_NAME','FOURTH_NAME']]
                rec['name'] = clean(' '.join(p for p in parts if p))
            else:
                rec['name'] = find_text(elem, 'FIRST_NAME')
                second = find_text(elem, 'SECOND_NAME')
                if second and rec['name']:
                    rec['name'] = clean(rec['name'] + ' ' + second)

            # aliases
            alias_containers = (
                find_all(elem, 'INDIVIDUAL_ALIAS') +
                find_all(elem, 'ENTITY_ALIAS')
            )
            for alias in alias_containers:
                a = find_text(alias, 'ALIAS_NAME')
                quality = find_text(alias, 'QUALITY')
                if a and a != rec['name'] and a not in rec['aliases']:
                    # prepend "Good quality" aliases
                    if quality and 'good' in quality.lower():
                        rec['aliases'].insert(0, a)
                    else:
                        rec['aliases'].append(a)

            # birthdates + birth places
            for dob in find_all(elem, 'INDIVIDUAL_DATE_OF_BIRTH'):
                d = parse_date(find_text(dob, 'DATE') or find_text(dob, 'YEAR') or find_text(dob, 'TYPE_OF_DATE'))
                if d and d not in rec['birthdates']:
                    rec['birthdates'].append(d)

            for pob in find_all(elem, 'INDIVIDUAL_PLACE_OF_BIRTH'):
                parts = [find_text(pob, f) for f in ['CITY','STATE_PROVINCE','COUNTRY']]
                place = clean(', '.join(p for p in parts if p))
                if place and place not in rec['birth_places']:
                    rec['birth_places'].append(place)

            # nationalities
            for nat in find_all(elem, 'NATIONALITY'):
                n = find_text(nat, 'VALUE')
                if n and n not in rec['nationalities']:
                    rec['nationalities'].append(n)

            # addresses
            for addr in find_all(elem, 'INDIVIDUAL_ADDRESS'):
                parts = [find_text(addr, f) for f in
                         ['STREET','CITY','STATE_PROVINCE','ZIP_CODE','COUNTRY']]
                full = clean(', '.join(p for p in parts if p))
                if full and full not in rec['addresses']:
                    rec['addresses'].append(full)

            # identifiers (documents)
            for doc in find_all(elem, 'INDIVIDUAL_DOCUMENT'):
                num = find_text(doc, 'NUMBER')
                if num:
                    rec['identifiers'].append({
                        'type': find_text(doc, 'TYPE_OF_DOCUMENT', 'TYPE_OF_DOCUMENT2'),
                        'number': num,
                        'country': find_text(doc, 'ISSUING_COUNTRY'),
                        'issued_by': find_text(doc, 'ISSUING_AUTHORITY'),
                        'valid_from': parse_date(find_text(doc, 'DATE_OF_ISSUE')),
                        'valid_to': parse_date(find_text(doc, 'DATE_OF_EXPIRY')),
                    })

            # listed on
            rec['listed_on'] = parse_date(find_text(elem, 'LISTED_ON'))

            # committees / programs
            for com in find_all(elem, 'COMMITTEES'):
                p = find_text(com, 'VALUE')
                if p and p not in rec['programs']:
                    rec['programs'].append(p)

            # remarks
            rec['remarks'] = find_text(elem, 'COMMENTS1', 'NOTE')

            if rec['name']:
                entities.append(rec)

    return entities


# ─────────────────────────────────────────────
# OFAC — SDN List
# ─────────────────────────────────────────────

def parse_ofac(content):
    entities = []
    root = etree.fromstring(content)

    for entry in find_all(root, 'sdnEntry'):
        rec = empty_record('OFAC')

        uid = find_text(entry, 'uid')
        rec['source_ids']['OFAC'] = uid

        sdn_type = find_text(entry, 'sdnType')
        rec['type'] = 'individual' if sdn_type and 'individual' in sdn_type.lower() else 'entity'

        # primary name
        fname = find_text(entry, 'firstName')
        lname = find_text(entry, 'lastName')
        if fname and lname:
            rec['name'] = clean(f'{fname} {lname}')
        elif lname:
            rec['name'] = lname
        elif fname:
            rec['name'] = fname

        # AKAs
        for aka in find_all(entry, 'aka'):
            parts = [find_text(aka, 'firstName'), find_text(aka, 'lastName')]
            a = clean(' '.join(p for p in parts if p))
            if not a:
                a = find_text(aka, 'lastName') or find_text(aka, 'firstName')
            ako_type = find_text(aka, 'type') or ''
            if a and a != rec['name'] and a not in rec['aliases']:
                if 'strong' in ako_type.lower() or 'f.k.a' in ako_type.lower():
                    rec['aliases'].insert(0, a)
                else:
                    rec['aliases'].append(a)

        # birthdates
        for dob in find_all(entry, 'dateOfBirthItem'):
            d = parse_date(find_text(dob, 'dateOfBirth'))
            if d and d not in rec['birthdates']:
                rec['birthdates'].append(d)

        # birth places
        for pob in find_all(entry, 'placeOfBirthItem'):
            p = find_text(pob, 'placeOfBirth')
            if p and p not in rec['birth_places']:
                rec['birth_places'].append(p)

        # nationalities
        for nat in find_all(entry, 'nationalityItem'):
            n = find_text(nat, 'nationality')
            if n and n not in rec['nationalities']:
                rec['nationalities'].append(n)

        # addresses
        for addr in find_all(entry, 'address'):
            parts = [find_text(addr, f) for f in
                     ['address1','address2','address3','city','stateOrProvince','postalCode','country']]
            full = clean(', '.join(p for p in parts if p))
            if full and full not in rec['addresses']:
                rec['addresses'].append(full)

        # identifiers (IDs, passports, etc.)
        for id_item in find_all(entry, 'idItem'):
            num = find_text(id_item, 'idNumber')
            if num:
                rec['identifiers'].append({
                    'type': find_text(id_item, 'idType'),
                    'number': num,
                    'country': find_text(id_item, 'idCountry'),
                    'issued_by': None,
                    'valid_from': None,
                    'valid_to': parse_date(find_text(id_item, 'expirationDate')),
                })

        # programs
        for prog in find_all(entry, 'program'):
            p = clean(prog.text)
            if p and p not in rec['programs']:
                rec['programs'].append(p)

        # remarks
        rec['remarks'] = find_text(entry, 'remarks')

        # listing date — not always present in SDN but check
        rec['listed_on'] = parse_date(find_text(entry, 'publishedDate'))

        if rec['name']:
            entities.append(rec)

    return entities


# ─────────────────────────────────────────────
# UK — OFSI Consolidated List (OFSI XML format)
# Similar to UN structure but with UK-specific fields
# ─────────────────────────────────────────────

def parse_uk(content):
    entities = []
    root = etree.fromstring(content)

    for tag, etype in [('INDIVIDUAL', 'individual'), ('ENTITY', 'entity')]:
        for elem in find_all(root, tag):
            rec = empty_record('UK')
            rec['type'] = etype
            rec['source_ids']['UK'] = find_text(elem, 'REFERENCE_NUMBER', 'UK_SANCTIONS_LIST_REF')

            # name
            if etype == 'individual':
                # OFSI uses NAME_TITLE, GIVEN_NAME, LAST_NAME or FIRST_NAME etc.
                given = find_text(elem, 'GIVEN_NAME', 'FIRST_NAME', 'NAME1')
                last  = find_text(elem, 'LAST_NAME',  'FAMILY_NAME', 'NAME6')
                mid1  = find_text(elem, 'MIDDLE_NAME', 'SECOND_NAME', 'NAME2')
                mid2  = find_text(elem, 'NAME3')
                mid3  = find_text(elem, 'NAME4')
                parts = [p for p in [given, mid1, mid2, mid3, last] if p]
                if not parts:
                    # fallback: try any NAME* field
                    parts = [find_text(elem, f'NAME{i}') for i in range(1, 7)
                             if find_text(elem, f'NAME{i}')]
                rec['name'] = clean(' '.join(parts))
            else:
                rec['name'] = (
                    find_text(elem, 'NAME', 'ENTITY_NAME', 'FIRST_NAME') or
                    find_text(elem, 'NAME1')
                )

            # aliases
            for alias in (find_all(elem, 'INDIVIDUAL_ALIAS') +
                          find_all(elem, 'ENTITY_ALIAS') +
                          find_all(elem, 'ALIAS')):
                a = find_text(alias, 'ALIAS_NAME', 'NAME', 'ALIAS_TYPE')
                if a and a != rec['name'] and a not in rec['aliases']:
                    rec['aliases'].append(a)

            # birthdates
            for dob in find_all(elem, 'INDIVIDUAL_DATE_OF_BIRTH'):
                d = parse_date(find_text(dob, 'DATE', 'YEAR'))
                if d and d not in rec['birthdates']:
                    rec['birthdates'].append(d)

            # birth places
            for pob in find_all(elem, 'INDIVIDUAL_PLACE_OF_BIRTH'):
                parts = [find_text(pob, f) for f in ['CITY','STATE_PROVINCE','COUNTRY']]
                place = clean(', '.join(p for p in parts if p))
                if place and place not in rec['birth_places']:
                    rec['birth_places'].append(place)

            # nationalities
            for nat in find_all(elem, 'NATIONALITY'):
                n = find_text(nat, 'VALUE', 'NATIONALITY')
                if n and n not in rec['nationalities']:
                    rec['nationalities'].append(n)

            # addresses
            for addr in find_all(elem, 'INDIVIDUAL_ADDRESS', 'ADDRESS', 'ENTITY_ADDRESS'):
                parts = [find_text(addr, f) for f in
                         ['STREET','ADDRESS1','ADDRESS2','CITY','STATE_PROVINCE','ZIP_CODE','POSTAL_CODE','COUNTRY']]
                full = clean(', '.join(p for p in parts if p))
                if full and full not in rec['addresses']:
                    rec['addresses'].append(full)

            # identifiers
            for doc in (find_all(elem, 'INDIVIDUAL_DOCUMENT') +
                        find_all(elem, 'PASSPORT') +
                        find_all(elem, 'DOCUMENT')):
                num = find_text(doc, 'NUMBER', 'DOCUMENT_NUMBER', 'PASSPORT_NUMBER')
                if num:
                    rec['identifiers'].append({
                        'type': find_text(doc, 'TYPE_OF_DOCUMENT', 'DOCUMENT_TYPE', 'TYPE'),
                        'number': num,
                        'country': find_text(doc, 'ISSUING_COUNTRY', 'COUNTRY_OF_ISSUE'),
                        'issued_by': find_text(doc, 'ISSUING_AUTHORITY'),
                        'valid_from': parse_date(find_text(doc, 'DATE_OF_ISSUE')),
                        'valid_to': parse_date(find_text(doc, 'DATE_OF_EXPIRY', 'EXPIRY_DATE')),
                    })

            # listing date
            rec['listed_on'] = parse_date(
                find_text(elem, 'LISTED_ON', 'DATE_LISTED', 'LISTING_DATE')
            )

            # programs / regimes
            rec['programs'] = [p for p in [
                find_text(elem, 'REGIME_NAME', 'REGIME', 'SANCTIONS_MEASURES')
            ] if p]

            # remarks
            rec['remarks'] = find_text(elem, 'COMMENTS1', 'ADDITIONAL_INFO', 'NOTES')

            if rec['name']:
                entities.append(rec)

    return entities


# ─────────────────────────────────────────────
# Merge — only on matching source UIDs
# (name-based dedup is your matching algorithm's job)
# ─────────────────────────────────────────────

# Known cross-list ID mappings (extend as needed)
# EU uses euReferenceNumber which often equals UN reference
CROSS_LIST_ID_MAP = {
    # (source_a, source_b): (field_a, field_b)
    ('EU', 'UN'): ('EU',  'UN'),   # EU logicalId vs UN reference
}

def _merge_into(base, extra):
    """Merge extra record fields into base record in-place."""
    for s in extra['source']:
        if s not in base['source']:
            base['source'].append(s)

    base['source_ids'].update(extra['source_ids'])

    for field in ['aliases', 'birthdates', 'birth_places', 'nationalities', 'addresses', 'programs']:
        for v in extra.get(field, []):
            if v not in base[field]:
                base[field].append(v)

    for ident in extra.get('identifiers', []):
        if ident not in base['identifiers']:
            base['identifiers'].append(ident)

    if not base['listed_on'] and extra.get('listed_on'):
        base['listed_on'] = extra['listed_on']

    if not base['remarks'] and extra.get('remarks'):
        base['remarks'] = extra['remarks']
    elif base['remarks'] and extra.get('remarks') and extra['remarks'] not in base['remarks']:
        base['remarks'] = base['remarks'] + ' | ' + extra['remarks']


def merge(datasets):
    """
    Merge strategy:
    1. Index all records by their source UIDs
    2. If a record from source B has an ID that matches a record from source A
       → merge into existing record
    3. Otherwise → add as new record
    """
    merged = []

    # uid_index: (source_name, uid) → index in merged list
    uid_index = {}

    for ds in datasets:
        for rec in ds:
            matched_idx = None

            # check if any of this record's source IDs match an existing record
            for src, uid in rec['source_ids'].items():
                if uid and (src, uid) in uid_index:
                    matched_idx = uid_index[(src, uid)]
                    break

            if matched_idx is not None:
                _merge_into(merged[matched_idx], rec)
                # index new IDs from the merged record
                for src, uid in rec['source_ids'].items():
                    if uid:
                        uid_index[(src, uid)] = matched_idx
            else:
                idx = len(merged)
                merged.append(rec)
                for src, uid in rec['source_ids'].items():
                    if uid:
                        uid_index[(src, uid)] = idx

    return merged


# ─────────────────────────────────────────────
# Debug: print XML structure to understand unknown formats
# ─────────────────────────────────────────────

def debug_xml_structure(content, max_depth=4, sample_entities=2):
    """Print first few entities and their tag tree — helps understand unknown schemata."""
    root = etree.fromstring(content)
    print(f"Root tag: {root.tag}")
    print(f"Root attribs: {dict(root.attrib)}")

    count = 0
    for child in root:
        if count >= sample_entities:
            break
        print(f"\n  [{count}] {local(child.tag)} attribs={dict(child.attrib)}")
        for sub in child:
            print(f"      {local(sub.tag)}: {repr(sub.text[:80] if sub.text else None)} attribs={dict(sub.attrib)}")
        count += 1


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    import os, sys

    data_dir = 'data'
    debug = '--debug' in sys.argv

    parsers = [
        ('EU',   'eu.xml',   parse_eu),
        ('UN',   'un.xml',   parse_un),
        ('OFAC', 'ofac.xml', parse_ofac),
        ('UK',   'uk.xml',   parse_uk),
    ]

    results = {}
    by_source = {}

    for name, filename, parser in parsers:
        path = os.path.join(data_dir, filename)
        try:
            with open(path, 'rb') as f:
                content = f.read()

            if debug:
                print(f'\n{"="*40}\nDEBUG: {name} XML structure\n{"="*40}')
                debug_xml_structure(content)

            entities = parser(content)
            results[name] = entities
            by_source[name] = len(entities)
            print(f'{name:6s}: {len(entities):>6,} entities parsed')

        except FileNotFoundError:
            print(f'{name:6s}: SKIP (file not found: {path})')
            results[name] = []
            by_source[name] = 0
        except Exception as e:
            print(f'{name:6s}: ERROR — {e}')
            import traceback; traceback.print_exc()
            results[name] = []
            by_source[name] = 0

    print()
    all_entities = list(results.values())
    merged = merge(all_entities)

    # stats
    multi_source = sum(1 for e in merged if len(e['source']) > 1)
    by_type = {}
    for e in merged:
        by_type[e['type']] = by_type.get(e['type'], 0) + 1

    print(f'Total after UID-merge : {len(merged):,}')
    print(f'Multi-source entities : {multi_source:,}')
    print(f'By type               : {by_type}')

    output = {
        'metadata': {
            'generated': datetime.utcnow().isoformat() + 'Z',
            'total': len(merged),
            'by_source': by_source,
            'multi_source_merged': multi_source,
            'by_type': by_type,
        },
        'entities': merged,
    }

    out_path = os.path.join(data_dir, 'all.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    print(f'\nOutput written to {out_path}')


if __name__ == '__main__':
    main()
