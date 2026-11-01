from testfix.test_quality import probe_html_helpers


def helper(stop):
    return 'from html.parser import HTMLParser\nclass Page(HTMLParser):\n def __init__(self):\n  super().__init__(); self.links=[]; self.active=False\n def handle_starttag(self,tag,attrs):\n  if tag=="a": self.links.append([dict(attrs).get("href"),""]); self.active=True\n def handle_endtag(self,tag):\n' + ('  if tag=="a": self.active=False\n' if stop else '  pass\n') + ' def handle_data(self,data):\n  if self.active: self.links[-1][1]+=data\n'


def test_concrete_helper_probe_detects_false_title_assertion():
    result=probe_html_helpers([{'path':'test_features.py','source':helper(False)}])
    assert result['checked']==1 and len(result['defects'])==1
    assert 'by author' in result['defects'][0]['counterexample']


def test_correct_nested_and_escaped_link_text_passes():
    result=probe_html_helpers([{'path':'test_features.py','source':helper(True)}])
    assert result['checked']==1 and result['defects']==[]


def test_nonstandard_pair_orientation_is_unrecognized_not_a_false_defect():
    source=helper(True).replace('dict(attrs).get("href"),""','"",dict(attrs).get("href")').replace('self.links[-1][1]+=data','self.links[-1][0]+=data')
    result=probe_html_helpers([{'path':'test_features.py','source':source}])
    assert result['checked']==0 and result['defects']==[]
    assert any('unknown_link_interface' in item for item in result['unrecognized'])
