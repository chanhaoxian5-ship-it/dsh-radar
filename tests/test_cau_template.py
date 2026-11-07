import unittest
from radar.sources.cau import CauAdapter
from radar.business import CONTENT,validate_business
class CauTemplateTest(unittest.TestCase):
 def test_synthetic_html_preserves_table_and_manifest(self):
  html=b'<meta name="ArticleTitle" content="Synthetic"><meta name="PubDate" content="2026-10-13 10:00"><nav>noise</nav><div id="zoom"><p>Submit <b>form</b></p><table><tr><td>date</td><td>2026-11-07</td></tr></table><p><a href="/module/download/x.doc">Form</a></p><img src="/poster.png" /></div><footer>noise</footer>'
  content=CauAdapter().extract(html);validate_business(CONTENT,content)
  self.assertEqual(content['published_date'],'2026-10-13');self.assertEqual(content['blocks'][1]['rows'],[['date','2026-11-07']]);self.assertEqual(len(content['attachments']),2);self.assertNotIn('noise',str(content))
 def test_challenge_or_incomplete_template_rejected(self):
  with self.assertRaises(ValueError):CauAdapter().extract(b'<html>security challenge</html>')

 def test_truncated_zoom_body_is_not_complete(self):
  with self.assertRaises(ValueError):CauAdapter().extract(b'<meta name="ArticleTitle" content="test"><div id="zoom"><p>truncated')
