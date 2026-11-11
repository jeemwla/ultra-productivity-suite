"""Fake PDH arrays: completeness must not be inferred from a nonempty result."""
import ctypes
import unittest
from pc_sentinel.backend.native_sensors import Pdh,_ITEM,PDH_MORE_DATA


class FakePdh:
    def __init__(self,values=(),statuses=(),empty=False,query_error=False,bad_second_count=False):
        self.items=(_ITEM*len(values))()
        for i,value in enumerate(values):
            self.items[i].szName=f'pid_{i+1}_engine'
            self.items[i].FmtValue.CStatus=statuses[i] if statuses else 0
            self.items[i].FmtValue.doubleValue=value
        self.empty,self.query_error,self.bad_second_count=empty,query_error,bad_second_count
    def PdhCollectQueryData(self,_query):return 1 if self.query_error else 0
    def PdhGetFormattedCounterArrayW(self,_h,_flags,size,count,buffer):
        size._obj.value=0 if self.empty else ctypes.sizeof(self.items)
        count._obj.value=len(self.items)+(1 if buffer is not None and self.bad_second_count else 0)
        if self.empty:return 0
        if buffer is None:return PDH_MORE_DATA
        ctypes.memmove(buffer,self.items,ctypes.sizeof(self.items));return 0


class PdhStatusTests(unittest.TestCase):
    def collect(self,fake):
        p=Pdh.__new__(Pdh);p.ok=True;p.counters={'counter':1};p.q=1;p.pdh=fake
        return p.collect(),p.last_status
    def test_all_valid_instances_are_complete(self):
        values,status=self.collect(FakePdh([0,5],[0,1]));self.assertEqual(len(values['counter']),2);self.assertEqual(status['counter'],'complete')
    def test_dropped_instance_marks_the_sample_partial(self):
        values,status=self.collect(FakePdh([1,5],[0,123]));self.assertEqual(len(values['counter']),1);self.assertEqual(status['counter'],'partial')
    def test_nonfinite_value_does_not_establish_zero(self):
        values,status=self.collect(FakePdh([float('nan')]));self.assertEqual(values['counter'],{});self.assertEqual(status['counter'],'partial')
    def test_successful_empty_array_differs_from_failed_query(self):
        self.assertEqual(self.collect(FakePdh(empty=True)),({'counter':{}},{'counter':'complete'}))
        self.assertEqual(self.collect(FakePdh(query_error=True)),({},{'counter':'unavailable'}))
    def test_changed_count_cannot_read_past_allocated_buffer(self):
        values,status=self.collect(FakePdh([1],bad_second_count=True));self.assertEqual(values,{});self.assertEqual(status['counter'],'invalid_size')


if __name__=='__main__':unittest.main()
