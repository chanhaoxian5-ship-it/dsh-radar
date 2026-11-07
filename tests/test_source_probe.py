import unittest
from radar.sources.cau_probe import parse

class SourceProbeTest(unittest.TestCase):
    def test_only_marks_partial_and_restricts_host(self):
        u='https://cast1.cau.edu.cn/art/2026/10/13/art_44372_1140438.html'
        result=parse(u,'<meta name="ArticleTitle" content="通知"><div id="zoom"><p>内容</p><a href="/x">附件</a></div>')
        self.assertEqual(result['external_key'],'1140438'); self.assertEqual(result['body_status'],'partial')
        self.assertEqual(result['links'],['https://cast1.cau.edu.cn/x'])
        with self.assertRaises(ValueError): parse('https://evil.example/art/2026/10/13/art_44372_1140438.html','')
