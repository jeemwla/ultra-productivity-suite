"""Fake-process regression cases, never open/stop real owner processes."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pc_sentinel.backend import system_tools as st


class FakeProcess:
    def __init__(self, pid, name, exe, created=10, readings=(0, 80), rss=1048576):
        self.pid=pid
        self.info={'name':name,'exe':exe,'create_time':created,
                   'memory_info':None if rss is None else SimpleNamespace(rss=rss)}
        self.readings=list(readings)
        self.calls=0
    def cpu_percent(self, interval):
        self.calls+=1
        value=self.readings.pop(0) if len(self.readings)>1 else self.readings[0]
        if isinstance(value,Exception):raise value
        return value


class ProcessCollectionTests(unittest.TestCase):
    def setUp(self):
        self.processes=[];self.clock=100;self.gpu={}
        self.patches=[patch.object(st,'_procs',{}),patch.object(st,'_proc_cache',{'at':0,'data':None}),
          patch.object(st.psutil,'process_iter',side_effect=lambda *_a,**_k:iter(self.processes)),
          patch.object(st.psutil,'cpu_count',return_value=4),patch.object(st,'_gpu_by_pid',side_effect=lambda:self.gpu),
          patch.object(st.time,'monotonic',side_effect=lambda:self.clock),patch.dict(os.environ,{'SystemRoot':r'C:\Windows'}),
          patch('pc_sentinel.backend.process_metadata.file_description',return_value=(None,False))]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
    def collect(self,*args,**kwargs):
        kwargs.setdefault('all_limit',512)
        kwargs.setdefault('include_details',True)
        return st.top_apps(*args,**kwargs)
    def sample_again(self):
        self.collect();self.clock+=3;return self.collect()
    def test_distinct_paths_do_not_merge_or_inherit_windows_description(self):
        self.processes=[FakeProcess(1,'dwm.exe',r'C:\Windows\System32\dwm.exe'),FakeProcess(2,'dwm.exe',r'C:\Users\Someone\dwm.exe')]
        result=self.sample_again();rows=result['all_apps']
        self.assertEqual(len(rows),2)
        self.assertEqual({r['display_name'] for r in rows},{'Desktop Window Manager','dwm'})
        self.assertEqual(sum(r['system_path_match'] for r in rows),1)
    def test_same_path_groups_children_and_preserves_identity(self):
        self.processes=[FakeProcess(1,'APP.EXE',r'C:\Apps\App.exe'),FakeProcess(2,'app.exe',r'c:\apps\app.exe')]
        r=self.sample_again()['all_apps'][0]
        self.assertEqual(r['count'],2);self.assertEqual(r['pids'],[1,2]);self.assertEqual(r['cpu'],40)
        self.assertEqual([p['pid'] for p in r['processes']],[1,2]);self.assertTrue(r['cpu_complete'])
    def test_unavailable_paths_stay_individual(self):
        self.processes=[FakeProcess(1,'svchost.exe',None),FakeProcess(2,'svchost.exe',None)]
        rows=self.sample_again()['all_apps'];self.assertEqual(len(rows),2)
        self.assertTrue(all(not r['system_path_match'] for r in rows))
    def test_first_interval_is_explicitly_warming_up(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe')]
        r=self.collect()['all_apps'][0];self.assertFalse(r['cpu_complete'])
        self.assertEqual(r['processes'][0]['cpu_sample_status'],'warming_up')
        self.assertEqual(self.collect()['by_cpu'],[])
    def test_pid_reuse_resets_the_interval(self):
        old=FakeProcess(1,'old.exe',r'C:\Apps\old.exe',10,(0,80));self.processes=[old];self.collect()
        self.clock+=3;fresh=FakeProcess(1,'new.exe',r'C:\Apps\new.exe',20,(0,40));self.processes=[fresh]
        r=self.collect()['all_apps'][0];self.assertFalse(r['cpu_complete']);self.assertEqual(old.calls,1)
        self.clock+=3;r=self.collect()['all_apps'][0];self.assertEqual(r['cpu'],10);self.assertTrue(r['cpu_complete'])
    def test_ended_process_state_is_discarded(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe')];self.collect()
        self.processes=[];self.clock+=3;self.assertEqual(self.collect()['all_apps'],[]);self.assertEqual(st._procs,{})
    def test_limit_is_applied_after_cache_and_responses_are_independent(self):
        self.processes=[FakeProcess(i,f'app{i}.exe',fr'C:\Apps\app{i}.exe') for i in range(1,5)]
        self.sample_again();small=self.collect(1,all_limit=2);large=self.collect(4,all_limit=4)
        self.assertEqual(len(small['by_cpu']),1);self.assertEqual(len(large['by_cpu']),4)
        self.assertTrue(small['truncated']);self.assertFalse(large['truncated'])
        small['all_apps'][0]['processes'].clear();self.assertEqual(len(self.collect()['all_apps'][0]['processes']),1)
    def test_cpu_filter_uses_unrounded_whole_machine_percent(self):
        self.processes=[FakeProcess(1,'below.exe',r'C:\Apps\below.exe',readings=(0,1.996)),FakeProcess(2,'edge.exe',r'C:\Apps\edge.exe',readings=(0,2.0))]
        r=self.sample_again();self.assertEqual([a['name'] for a in r['by_cpu']],['edge'])
        self.assertEqual(len(r['all_apps']),2);self.assertEqual(r['cpu_percent_basis'],'whole_machine')
    def test_memory_sort_does_not_hide_idle_large_apps(self):
        self.processes=[FakeProcess(1,'idle.exe',r'C:\Apps\idle.exe',readings=(0,0),rss=100*1048576)]
        r=self.sample_again();self.assertEqual(r['by_cpu'],[]);self.assertEqual(r['by_mem'][0]['mem_mb'],100)
    def test_gpu_threshold_and_availability_are_independent(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe',readings=(0,0))];self.gpu={1:{'gpu':.5,'vram_mb':10}}
        r=self.sample_again();self.assertTrue(r['gpu_available']);self.assertEqual(len(r['by_gpu']),1);self.assertEqual(r['by_cpu'],[])
    def test_complete_gpu_sample_establishes_absent_old_process_has_no_engine(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe'),FakeProcess(2,'app.exe',r'C:\Apps\app.exe')]
        self.gpu={'processes':{1:{'gpu':30,'vram_mb':10}},'engine_complete':True,'memory_complete':True,'observed_at':100}
        r=self.collect()['all_apps'][0];self.assertTrue(r['gpu_complete']);self.assertEqual(r['gpu'],30)
        child=r['processes'][1];self.assertEqual(child['gpu'],0);self.assertEqual(child['vram_mb'],0);self.assertEqual(child['gpu_sample_status'],'no_gpu_engine_instance')
    def test_partial_counter_sample_does_not_infer_zero_for_absent_pid(self):
        self.processes=[FakeProcess(1,'app.exe',None),FakeProcess(2,'other.exe',None)]
        self.gpu={'processes':{1:{'gpu':30}},'engine_complete':False,'memory_complete':False,'observed_at':100}
        missing=next(r for r in self.collect()['all_apps'] if r['pids']==[2]);self.assertIsNone(missing['processes'][0]['gpu']);self.assertFalse(missing['gpu_complete'])
    def test_new_process_after_gpu_sample_remains_unknown(self):
        self.processes=[FakeProcess(1,'app.exe',None,created=101)]
        self.gpu={'processes':{},'engine_complete':True,'memory_complete':True,'observed_at':100}
        child=self.collect()['all_apps'][0]['processes'][0];self.assertIsNone(child['gpu']);self.assertIsNone(child['vram_mb'])
    def test_old_gpu_pid_data_is_not_attributed_to_a_reused_process_id(self):
        self.processes=[FakeProcess(1,'replacement.exe',None,created=101)]
        self.gpu={'processes':{1:{'gpu':90,'vram_mb':50,'engines':{'old_engine':90}}},'engine_complete':True,'memory_complete':True,'observed_at':100}
        group=self.collect()['all_apps'][0];child=group['processes'][0]
        self.assertIsNone(child['gpu']);self.assertIsNone(child['vram_mb']);self.assertEqual(group['gpu'],0);self.assertFalse(group['gpu_complete'])
    def test_group_gpu_uses_busiest_physical_engine_not_sum_of_unrelated_engines(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe'),FakeProcess(2,'app.exe',r'C:\Apps\app.exe')]
        self.gpu={'processes':{1:{'gpu':60,'engines':{'gpuA_3D':60}},2:{'gpu':70,'engines':{'gpuB_3D':70}}},'engine_complete':True,'memory_complete':False,'observed_at':100}
        r=self.collect()['all_apps'][0];self.assertEqual(r['gpu'],70);self.assertEqual(r['gpu_percent_basis'],'busiest_reported_physical_engine')
        self.assertNotIn('_gpu_engine_sums',r)
    def test_group_gpu_sums_process_use_on_the_same_engine(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe'),FakeProcess(2,'app.exe',r'C:\Apps\app.exe')]
        self.gpu={'processes':{1:{'gpu':30,'engines':{'gpuA_3D':30}},2:{'gpu':40,'engines':{'gpuA_3D':40}}},'engine_complete':True,'memory_complete':False,'observed_at':100}
        self.assertEqual(self.collect()['all_apps'][0]['gpu'],70)
    def test_access_denied_is_reported_without_a_cpu_success_claim(self):
        self.processes=[FakeProcess(1,'app.exe',None,readings=(st.psutil.AccessDenied(1),))]
        r=self.collect()['all_apps'][0];self.assertFalse(r['cpu_complete']);self.assertEqual(r['processes'][0]['cpu_sample_status'],'unavailable')
    def test_unknown_creation_time_never_reuses_an_interval(self):
        p=FakeProcess(1,'app.exe',None,created=None);self.processes=[p];r=self.sample_again()['all_apps'][0]
        self.assertFalse(r['cpu_complete']);self.assertEqual(r['processes'][0]['cpu_sample_status'],'identity_unavailable')
        self.assertIsNone(r['processes'][0]['cpu'])
    def test_missing_resource_readings_are_not_measured_zero(self):
        self.processes=[FakeProcess(1,'app.exe',None,rss=None)]
        r=self.collect()['all_apps'][0];child=r['processes'][0]
        self.assertIsNone(child['cpu']);self.assertIsNone(child['mem_mb']);self.assertIsNone(child['gpu']);self.assertIsNone(child['vram_mb'])
        self.assertFalse(r['cpu_complete']);self.assertFalse(r['mem_complete']);self.assertFalse(r['gpu_complete'])
    def test_reported_zero_resources_remain_zero(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe',readings=(0,0),rss=0)];self.gpu={1:{'gpu':0,'vram_mb':0}}
        child=self.sample_again()['all_apps'][0]['processes'][0]
        self.assertEqual(child['cpu'],0);self.assertEqual(child['mem_mb'],0);self.assertEqual(child['gpu'],0)
    def test_all_invalid_limits_are_rejected(self):
        for v in [0,-1,513,True,None,1.5,'8']:
            with self.subTest(v=v),self.assertRaises(ValueError):self.collect(v)
            if v != 0:
                with self.subTest(all_limit=v),self.assertRaises(ValueError):self.collect(all_limit=v)

    def test_default_poll_is_compact_and_omits_child_records(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe')]
        result=st.top_apps()
        self.assertEqual(result['all_apps'],[]);self.assertFalse(result['all_included'])
        self.assertNotIn('processes',result['by_mem'][0]);self.assertNotIn('pids',result['by_mem'][0])
    def test_unknown_group_never_returns_a_different_process(self):
        self.processes=[FakeProcess(1,'app.exe',r'C:\Apps\app.exe')]
        self.assertEqual(self.collect(group_id='missing')['all_apps'],[])

    def test_metadata_budget_counts_misses_and_progresses_past_cached_top_rows(self):
        self.processes=[FakeProcess(i,f'app{i}.exe',fr'C:\Apps\app{i}.exe') for i in range(30)]
        seen=set()
        def description(path,*,cached_only=False,with_status=False):
            if path in seen:return ('Described app',False)
            if cached_only:return (None,False)
            seen.add(path);return ('Described app',True)
        with patch('pc_sentinel.backend.process_metadata.file_description',side_effect=description):
            self.collect();self.assertEqual(len(seen),24)
            result=self.collect();self.assertEqual(len(seen),30)
            self.assertTrue(all(r['name_source']=='file_description' for r in result['all_apps']))


class ProcessApiTests(unittest.TestCase):
    def test_route_limits_and_on_demand_details(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI();st.register(app)
        with TestClient(app) as client,patch.object(st,'top_apps',return_value={'all_apps':[{'group_id':'example','processes':[{'pid':1}]}],'observed_at':123}) as collect:
            self.assertEqual(client.get('/api/processes?all_limit=513').status_code,422)
            self.assertEqual(client.get('/api/processes?limit=0').status_code,422)
            self.assertEqual(client.get('/api/processes').status_code,200)
            collect.assert_called_with(8,all_limit=0)
            result=client.get('/api/processes/example');self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['group']['processes'][0]['pid'],1)
            collect.assert_called_with(1,include_details=True,group_id='example')
        with TestClient(app) as client,patch.object(st,'top_apps',return_value={'all_apps':[],'observed_at':123}):
            self.assertEqual(client.get('/api/processes/ended-group').status_code,404)


if __name__=='__main__':unittest.main()
