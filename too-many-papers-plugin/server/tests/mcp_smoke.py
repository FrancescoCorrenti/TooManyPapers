"""Protocol smoke test. Use --browser to leave an isolated UI at localhost:3737."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    data_dir = Path(tempfile.mkdtemp(prefix='too-many-papers-smoke-'))
    fixtures = {
        '_papers.json': {'_meta': {}, 'papers': {'P001': {
            'title': 'Synthetic retrieval study (test fixture)', 'authors': ['Test Author'], 'year': 2026,
            'abstract': 'This synthetic fixture describes reranking retrieved documents before answer generation. '
                        'It reports no measured accuracy or latency results.',
            'source_verified': 'test-fixture', 'concepts': [], 'notes': '', 'file': '', 'discovered': '2026-09-15',
        }}},
        '_venues.json': {'venues': {}},
        '_graph.json': {'_meta': {'version': '2.0'}, 'nodes': {
            'PROJ-TEST': {'type': 'project', 'name': 'Retrieval test project', 'status': 'active',
                          'description': 'Evaluate relevance of retrieved documents.'},
            'WP-001': {'type': 'waypoint', 'name': 'Evaluate reranking', 'description': 'Compare retrieved documents.'},
            'EP-001': {'type': 'endpoint', 'name': 'Improve retrieval'},
        }, 'edges': [
            {'src': 'WP-001', 'tgt': 'EP-001', 'type': 'leads_to'},
            {'src': 'EP-001', 'tgt': 'PROJ-TEST', 'type': 'part_of'},
            {'src': 'WP-001', 'tgt': 'P001', 'type': 'inspired_by'},
        ]},
    }
    for name, content in fixtures.items():
        (data_dir / name).write_text(json.dumps(content), encoding='utf-8')
    server = Path(__file__).resolve().parents[1] / '_scripts/mcp_server.py'
    env = {**os.environ, 'TOO_MANY_PAPERS_DATA_DIR': str(data_dir)}
    params = StdioServerParameters(command=sys.executable, args=[str(server)], env=env)

    def cli(*args):
        """Edge commands are web-UI-only now; exercise their guards through the CLI."""
        r = subprocess.run([sys.executable, str(server.with_name('papers_api.py')), *args],
                           capture_output=True, text=True, encoding='utf-8', env=env)
        return r.stdout + r.stderr
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            removed = {'graph_add_waypoint', 'graph_add_endpoint', 'graph_add_edge', 'graph_remove_edge',
                       'graph_add_nodes_bulk', 'graph_interact', 'graph_lint', 'briefing_generate',
                       'venues_add', 'papers_list', 'papers_hide'}
            assert not removed & tools, removed & tools
            assert {'graph_link_project_paper', 'graph_project_context', 'graph_review_queue',
                    'papers_find', 'citations_sync'} <= tools

            async def call(name, args=None, error=False):
                result = await session.call_tool(name, args or {})
                text = '\n'.join(c.text for c in result.content if c.type == 'text')
                if error:
                    assert 'ERROR' in text, text
                else:
                    assert not result.isError and 'ERROR' not in text, text
                return text

            queue = json.loads(await call('graph_review_queue'))
            assert {'WP-001', 'EP-001'} <= queue.keys()
            assert (data_dir / '_graph.pre-v3.json').exists()
            for a, b in [('PROJ-TEST', 'P001'), ('P001', 'PROJ-TEST'), ('WP-001', 'PROJ-TEST')]:
                assert 'ERROR' in cli('graph-add-edge', a, b, 'relevant_to')
            assert 'P001' in await call('papers_find', {'author': 'test author', 'year': 2026})
            assert 'No papers match' in await call('papers_find', {'year': 1999})
            await call('papers_update', {'id': 'P001', 'payload': json.dumps({'venue': 'Test Venue'})})
            await call('papers_update', {'id': 'P001', 'payload': json.dumps({'venue': 'test venue'})})
            venues = json.loads((data_dir / '_venues.json').read_text(encoding='utf-8'))['venues']
            assert [v['name'] for v in venues.values()] == ['Test Venue'], venues
            assert 'P001' in await call('papers_find', {'venue': 'test ven'})
            await call('papers_update', {'id': 'P001', 'payload': json.dumps({'venue': 'MICCAI 2026'})}, error=True)
            ctx = json.loads(await call('graph_project_context', {'project_id': 'PROJ-TEST', 'paper_id': 'P001'}))
            assert 'reranking' in ctx['paper']['abstract']
            args = dict(project_id='PROJ-TEST', paper_id='P001', name='Reranking for retrieval evaluation',
                        relevance='The project evaluates document relevance; reranking is a candidate method to investigate.',
                        relevant_summary='The supplied abstract describes reranking documents before generating an answer.',
                        evidence='Synthetic test fixture, abstract, sentence 1.',
                        limitations='Abstract only. The fixture contains no measured accuracy or latency results; no improvement is established.',
                        context_token=ctx['context_token'], legacy_idea_ids=['WP-001', 'EP-001'])
            await call('graph_link_project_paper', {**args, 'evidence': ' '}, error=True)
            first = json.loads(await call('graph_link_project_paper', args))
            second = json.loads(await call('graph_link_project_paper', args))
            assert first['idea_id'] == second['idea_id']
            iid = first['idea_id']
            assert not json.loads(await call('graph_review_queue'))
            await call('graph_remove_node', {'id': iid}, error=True)
            assert 'ERROR' in cli('graph-remove-edge', iid, 'P001')
            await call('graph_update_node', {'id': 'PROJ-TEST', 'payload': json.dumps({'description': 'Evaluate retrieval and latency.'})})
            assert iid in json.loads(await call('graph_review_queue'))
            await call('graph_link_project_paper', args, error=True)
            ctx = json.loads(await call('graph_project_context', {'project_id': 'PROJ-TEST', 'paper_id': 'P001'}))
            args['context_token'] = ctx['context_token']
            await call('graph_link_project_paper', args)
            assert 'ERROR' not in cli('graph-remove-edge', 'P001', 'PROJ-TEST', '--type', 'relevant_to')
            ctx = json.loads(await call('graph_project_context', {'project_id': 'PROJ-TEST'}))
            assert ctx['ideas'][iid]['review_state'] == 'detached'
            assert json.loads(await call('graph_link_project_paper', args))['idea_id'] == iid
            assert not json.loads(await call('graph_review_queue'))
            if '--browser' in sys.argv:
                print(await call('webui_launch'))
                print('PASS: MCP protocol smoke checks. Browser test server ready; Ctrl+C to stop.', flush=True)
                print(f'Test data: {data_dir}', flush=True)
                await asyncio.Event().wait()
            else:
                await call('papers_delete', {'ids': 'P001'})
                ctx = json.loads(await call('graph_project_context', {'project_id': 'PROJ-TEST'}))
                assert not ctx['paper_ids']
                assert ctx['ideas'][iid]['review_state'] == 'detached'
                assert ctx['ideas'][iid]['description']
    print('PASS: MCP initialize/list/call, migration, validation, pair uniqueness, stale/refresh, unlink/relink.')
    print(f'Test data: {data_dir}')


if __name__ == '__main__':
    asyncio.run(main())
