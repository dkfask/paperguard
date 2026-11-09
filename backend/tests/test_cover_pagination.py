import copy
import hashlib

import pytest
from pydantic import ValidationError

from app.services.office_layout import _cover_policy, _validated_cover_evidence, cover_pagination_result
from app.services.source_template_profile import CoverPaginationSettings, ProfileError
from app.services.word_native_layout import _WORD_COVER_SCRIPT, WordNativeLayoutError, _parse_cover_measurement


def policy_profile(offsets=None, *, new_page=True):
    return {
        'source_sha256': '1' * 64,
        'specification_sha256': '2' * 64,
        'toc_active': True,
        'headings': [{'index': 1, 'kind': 'abstract', 'key': 'abstract', 'bookmark_name': 'PGHabstract'}],
        'cover_pagination': {'boundary_key': 'abstract', 'boundary_bookmark': 'PGHabstract',
                             'allowed_blank_offsets': offsets or [], 'require_boundary_new_page': new_page,
                             'source': {'kind': 'explicit_template_confirmation', 'source_sha256': '1' * 64,
                                        'specification_sha256': '2' * 64}},
    }


def page(number, *, text=False, plain=None, inline=0, table=0, floating=0):
    return {'page': number, 'text_present': text, 'plain_text_candidate': text if plain is None else plain, 'inline_shapes': inline,
            'tables': table, 'floating_shapes': floating}


def evidence(pages):
    return {'scope': 'word_cover_main_story_pages_only', 'source_sha256': '3' * 64,
            'word_version': '16.113.3', 'macos_version': '15.7.7', 'machine': 'arm64',
            'probe_sha256': hashlib.sha256(_WORD_COVER_SCRIPT.encode()).hexdigest(),
            'page_count': 10, 'pagination_stable': True, 'plain_text_context': True, 'boundary_bookmark': 'PGHabstract',
            'boundary_page': len(pages), 'pages': pages}


def word(boundary_page):
    return {'word_version': '16.113.3', 'macos_version': '15.7.7', 'machine': 'arm64',
            'page_count': 10, 'extra_bookmarks': [{'bookmark': 'PGHabstract', 'physical_page': boundary_page}]}


def evaluate(pages, offsets=None, *, new_page=True):
    profile = policy_profile(offsets, new_page=new_page)
    measured = evidence(pages)
    _validated_cover_evidence(measured, _cover_policy(profile), word(len(pages)), '3' * 64)
    return cover_pagination_result(profile, {'word_cover': measured})


def test_normal_multipage_and_intentionally_blank_cover():
    assert evaluate([page(1, text=True), page(2)])['ok']
    assert evaluate([page(1, text=True, table=1), page(2, text=True, inline=1), page(3)])['ok']
    assert evaluate([page(1, text=True), page(2), page(3)], [1])['ok']


@pytest.mark.parametrize('unknown', [page(2, text=True, plain=False), page(2, table=1),
                                     page(2, inline=1), page(2, floating=1)])
def test_unverifiable_page_is_not_treated_as_nonblank(unknown):
    result = evaluate([page(1, text=True), unknown, page(3)])
    assert not result['ok'] and result['unverifiable_pages'] == [2]
    assert evaluate([page(1, text=True), unknown, page(3)], [1])['ok']


def test_nonplain_background_and_unknown_boundary_do_not_pass():
    profile = policy_profile()
    measured = evidence([page(1, text=True), page(2)])
    measured['plain_text_context'] = False
    assert not cover_pagination_result(profile, {'word_cover': measured})['ok']
    result = evaluate([page(1, text=True), page(2, text=True, plain=False)])
    assert not result['ok'] and '无法验证' in result['reason']


def test_unexpected_empty_cover_page_and_shared_boundary_fail():
    result = evaluate([page(1, text=True), page(2), page(3)])
    assert not result['ok'] and result['unexpected_blank_pages'] == [2]
    assert not evaluate([page(1, text=True), page(2, text=True)])['ok']
    assert evaluate([page(1, text=True), page(2, text=True)], new_page=False)['ok']
    # A cover sharing the very first page with the boundary is allowed only
    # when the confirmed template policy explicitly permits it.
    assert evaluate([page(1, text=True)], new_page=False)['ok']
    assert not evaluate([page(1, text=True)], new_page=True)['ok']
    assert not evaluate([page(1)], new_page=False)['ok']
    assert not evaluate([page(1), page(2)], [1])['ok']
    # An allowed blank immediately before the heading does not allow an earlier one.
    assert evaluate([page(1, text=True), page(2), page(3), page(4)], [1])['unexpected_blank_pages'] == [2]


@pytest.mark.parametrize('value', [[0], [64], [True], [1, 1], ['1']])
def test_confirmed_allowed_page_offsets_must_be_distinct_integers(value):
    with pytest.raises(ValidationError):
        CoverPaginationSettings(allowed_blank_offsets=value)


@pytest.mark.parametrize('damage', ['source', 'specification', 'boundary', 'offsets', 'toc'])
def test_cover_policy_is_bound_to_confirmed_source_and_first_heading(damage):
    profile = policy_profile()
    if damage == 'source':
        profile['cover_pagination']['source']['source_sha256'] = '0' * 64
    elif damage == 'specification':
        profile['cover_pagination']['source']['specification_sha256'] = '0' * 64
    elif damage == 'boundary':
        profile['cover_pagination']['boundary_bookmark'] = 'PGHother'
    elif damage == 'offsets':
        profile['cover_pagination']['allowed_blank_offsets'] = [2, 1]
    else:
        profile['toc_active'] = False
    with pytest.raises(ProfileError):
        _cover_policy(profile)


@pytest.mark.parametrize('damage', ['source', 'script', 'environment', 'boundary', 'missing_page', 'page_number', 'boolean_count', 'context', 'candidate'])
def test_cover_evidence_rejects_mismatch_or_incomplete_pages(damage):
    measured = evidence([page(1, text=True), page(2)])
    if damage == 'source':
        measured['source_sha256'] = '0' * 64
    elif damage == 'script':
        measured['probe_sha256'] = '0' * 64
    elif damage == 'environment':
        measured['word_version'] = '16.1'
    elif damage == 'boundary':
        measured['boundary_page'] = 1
    elif damage == 'missing_page':
        measured['pages'].pop()
    elif damage == 'page_number':
        measured['pages'][1]['page'] = 1
    elif damage == 'boolean_count':
        measured['pages'][0]['tables'] = True
    elif damage == 'context':
        measured['plain_text_context'] = 1
    else:
        measured['pages'][1]['plain_text_candidate'] = True
    with pytest.raises(ProfileError):
        _validated_cover_evidence(measured, _cover_policy(policy_profile()), word(2), '3' * 64)


def test_cover_probe_rejects_unstable_or_incomplete_two_pass_data():
    good = '\n'.join(['V|16.113.3', 'T|1|4', 'B|1|2|15', 'P|1|1|1|1|0|1|0', 'P|1|2|0|0|0|0|0',
                      'T|2|4', 'B|2|2|15', 'P|2|1|1|1|0|1|0', 'P|2|2|0|0|0|0|0'])
    version, count, boundary, pages = _parse_cover_measurement(good)
    assert (version, count, boundary, pages) == ('16.113.3', 4, 2, [page(1, text=True, table=1), page(2)])
    for damaged in (good.replace('P|2|2|0|0|0|0|0', 'P|2|2|1|0|0|0|0'),
                    good.replace('P|2|2|0|0|0|0|0', ''), good.replace('T|2|4', 'T|2|5'),
                    good.replace('P|2|2|0|0|0|0|0', 'P|2|2|0|1|0|0|0')):
        with pytest.raises(WordNativeLayoutError):
            _parse_cover_measurement(damaged)
