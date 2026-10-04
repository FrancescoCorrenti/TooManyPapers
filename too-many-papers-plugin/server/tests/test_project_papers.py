import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_scripts'))
import project_papers as pp


class ProjectPapersTests(unittest.TestCase):
    def setUp(self):
        self.papers = {'P001': {'title': 'Test paper', 'abstract': 'A documented method.'}}
        self.graph = {'_meta': {'version': '3.0'}, 'nodes': {
            'PROJ-A': {'type': 'project', 'name': 'A', 'description': 'Evaluate retrieval'},
            'PROJ-B': {'type': 'project', 'name': 'B', 'description': 'Evaluate cost'},
        }, 'edges': []}

    def link(self, project='PROJ-A', **overrides):
        payload = dict(name='Useful method', relevance='Helps evaluate retrieval.',
                       relevant_summary='The method compares ranked documents.', evidence='Abstract, sentence 1.',
                       limitations='Abstract only; no full-text evidence.',
                       context_token=pp.context(self.graph['nodes'][project], self.papers['P001']))
        payload.update(overrides)
        return pp.association(self.graph, self.papers, project, 'P001', payload)

    def test_atomic_validation(self):
        before = copy.deepcopy(self.graph)
        for field in ('name', 'relevance', 'relevant_summary', 'evidence', 'limitations', 'context_token'):
            with self.assertRaises(ValueError):
                self.link(**{field: ' '})
            self.assertEqual(before, self.graph)

    def test_pair_uniqueness_and_history(self):
        iid = self.link()
        self.assertEqual(iid, self.link(relevance='Updated analysis.'))
        self.assertEqual(2, len(self.graph['edges']))
        self.assertEqual(1, len(self.graph['nodes'][iid]['analysis_history']))
        self.assertNotEqual(iid, self.link('PROJ-B'))

    def test_stale_and_refresh(self):
        iid = self.link()
        idea = self.graph['nodes'][iid]
        self.assertEqual('current', pp.review_state(idea, self.graph, self.papers))
        self.graph['nodes']['PROJ-A']['status'] = 'writing'
        self.papers['P001']['read'] = True
        self.assertEqual('current', pp.review_state(idea, self.graph, self.papers))
        self.graph['nodes']['PROJ-A']['description'] = 'New scope'
        self.assertEqual('stale', pp.review_state(idea, self.graph, self.papers))
        self.link()
        self.assertEqual('current', pp.review_state(idea, self.graph, self.papers))
        self.papers['P001']['abstract'] = 'Corrected findings'
        self.assertEqual('stale', pp.review_state(idea, self.graph, self.papers))
        self.link()
        idea['description'] += 'Manual edit'
        self.assertEqual('stale', pp.review_state(idea, self.graph, self.papers))

    def test_detach_preserves_analysis(self):
        iid = self.link()
        self.graph['edges'] = [e for e in self.graph['edges'] if e['type'] != 'relevant_to']
        self.assertEqual('detached', pp.review_state(self.graph['nodes'][iid], self.graph, self.papers))
        self.assertTrue(self.graph['nodes'][iid]['description'])
        self.assertEqual(iid, self.link())

    def test_multiple_projects_require_separate_legacy_reviews(self):
        self.graph['nodes']['OLD'] = {
            'type': 'idea', 'legacy': {'project_ids': ['PROJ-A', 'PROJ-B'], 'resolved_by': []}}
        self.link(legacy_idea_ids=['OLD'])
        self.assertEqual('needs_analysis', pp.review_state(self.graph['nodes']['OLD'], self.graph, self.papers))
        self.link('PROJ-B', legacy_idea_ids=['OLD'])
        self.assertEqual('incorporated', pp.review_state(self.graph['nodes']['OLD'], self.graph, self.papers))

    def test_migration_cyclic_and_orphaned_legacy(self):
        self.graph['_meta']['version'] = '2.0'
        self.graph['nodes'].update({
            'WP-001': {'type': 'waypoint', 'name': 'Try method', 'description': 'Preserve me', 'status': 'failed'},
            'EP-1': {'type': 'endpoint', 'name': 'Evaluate'},
            'WP-ORPHAN': {'type': 'waypoint', 'name': 'Unassigned'},
        })
        self.graph['edges'] = [
            {'src': 'EP-1', 'tgt': 'PROJ-A', 'type': 'part_of'},
            {'src': 'WP-001', 'tgt': 'EP-1', 'type': 'leads_to'},
            {'src': 'EP-1', 'tgt': 'WP-001', 'type': 'leads_to'},
            {'src': 'WP-001', 'tgt': 'P001', 'type': 'inspired_by'},
        ]
        self.assertTrue(pp.migrate(self.graph, self.papers))
        self.assertFalse(pp.migrate(self.graph, self.papers))
        idea = self.graph['nodes']['WP-001']
        self.assertEqual('idea', idea['type'])
        self.assertEqual('Preserve me', idea['description'])
        self.assertEqual('failed', idea['legacy']['original']['status'])
        self.assertEqual(['PROJ-A'], idea['legacy']['project_ids'])
        self.assertEqual(['P001'], idea['legacy']['candidate_paper_ids'])
        self.assertEqual('needs_analysis', pp.review_state(idea, self.graph, self.papers))
        self.assertEqual([], self.graph['nodes']['WP-ORPHAN']['legacy']['project_ids'])
        self.assertEqual(1, len(self.graph['edges']))
        self.assertEqual(3, len(self.graph['_meta']['legacy_edges']))
        iid = self.link(legacy_idea_ids=['WP-001'])
        self.assertEqual([iid], idea['legacy']['resolved_by'])

    def test_existing_direct_links_get_pending_idea(self):
        self.graph['_meta']['version'] = '2.0'
        self.graph['edges'] = [{'src': 'PROJ-A', 'tgt': 'P001', 'type': 'connected_to'}]
        pp.migrate(self.graph, self.papers)
        edge = self.graph['edges'][0]
        self.assertEqual(('P001', 'PROJ-A', 'relevant_to'), (edge['src'], edge['tgt'], edge['type']))
        self.assertEqual('needs_analysis', pp.review_state(self.graph['nodes'][edge['idea_id']], self.graph, self.papers))


if __name__ == '__main__':
    unittest.main()
