const {test}=require('node:test');
const assert=require('node:assert/strict');
const P=require('../pc_sentinel/frontend/sentinel_panels.js');
const links={lhm:'https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases',pawnio:'https://github.com/namazso/PawnIO.Setup/releases'};

test('sensor banner explains the missing piece, links official pages and hides when sensors work',()=>{
 assert.equal(P.sensorBanner({state:'ok',links}),null);assert.equal(P.sensorBanner(null),null);
 const b=P.sensorBanner({state:'driver_missing',title:'Sensor driver missing',text:'PawnIO isn\'t installed',links});
 assert.equal(b.title,'Sensor driver missing');assert.match(b.html,/PawnIO\.Setup\/releases/);assert.match(b.html,/LibreHardwareMonitor\/releases/);assert.match(b.html,/WinRing0/);assert.equal(b.setup,false);
 assert.equal(P.sensorBanner({state:'lhm_missing',links}).setup,true);
 assert.equal(P.missingReading({state:'driver_missing'}),'sensor driver missing');assert.equal(P.missingReading(undefined),'needs full sensors');
 assert.doesNotMatch(P.sensorBanner({state:'lhm_missing',text:'<img src=x>',links}).html,/<img src=x>/);
});
test('benchmark panel never claims a gain without both measurements, and respects the noise',()=>{
 assert.match(P.benchHtml(null),/not measured/);assert.doesNotMatch(P.benchHtml(null),/better|worse/);
 const before={ts:1,single_mbps:2000,multi_mbps:9000,wake_us:40};
 assert.match(P.benchHtml({before}),/measure again to compare/);assert.doesNotMatch(P.benchHtml({before}),/% better/);
 const after={ts:2,single_mbps:2200,multi_mbps:9100,wake_us:30};
 const html=P.benchHtml({before,after,compare:{single_mbps:{better_pct:10,noise_pct:3,real:true},multi_mbps:{better_pct:1.1,noise_pct:8,real:false},wake_us:{better_pct:-5,noise_pct:3,real:true}}});
 assert.match(html,/10\.0% better/);assert.match(html,/No measurable difference \(within ±8\.0%\)/);assert.match(html,/5\.0% worse/);assert.match(html,/2200 MB\/s/);assert.match(html,/30\.0 µs/);
 assert.match(P.benchHtml({},true),/Measuring/);
});
const amd={name:'atieclxx',level:'warning',avg_bps_1h:2.5e6,active_share_1h:0.98,bytes_today:4e10,per_day_estimate:2.16e11,
 known_issue:{title:'AMD External Events Utility',retired:false,explanation:'A known AMD Radeon driver bug',fixes:[{title:'Disable',steps:'services.msc',command:'Set-Service x',undo:'Set-Service y'}],check:'Get-CimInstance',sources:[{title:'VideoCardz',url:'https://videocardz.com/x'}]}};
test('writers card: empty state, warm-up, flagged rows with plain sentences and real tables',()=>{
 assert.match(P.writersHtml(null),/Reading/);assert.match(P.writersHtml({available:false}),/aren't available/);
 const quiet=P.writersHtml({available:true,observed_minutes_1h:60,top:[{name:'chrome',level:null,avg_bps_1h:1000,active_share_1h:.2,bytes_today:5e8,per_day_estimate:8.6e7}]});
 assert.match(quiet,/No sustained writer flagged in the available samples/);assert.match(quiet,/<th scope="col">Program<\/th>/);assert.match(quiet,/Normal/);
 assert.match(P.writersHtml({available:true,observed_minutes_1h:12,top:[]}),/12 of the 60 minutes/);
 const hit=P.writersHtml({available:true,observed_minutes_1h:60,top:[amd],drive_write_bps:3e6});
 assert.match(hit,/AMD External Events Utility is writing up to 2\.5 MB\/s almost all the time \(98% of the last hour\)/);assert.match(hit,/does not establish/);assert.match(hit,/up to 216\.0 GB a day/);assert.match(hit,/>Warning</);assert.match(hit,/All drives together: 3\.0 MB\/s/);assert.match(hit,/not flash wear/);
 assert.match(hit,/capped every minute by what all drives actually received/);assert.doesNotMatch(hit,/traffic between programs, not drive writes/);
 const ignored=P.writersHtml({available:true,observed_minutes_1h:60,top:[amd]},['atieclxx']);assert.doesNotMatch(ignored,/is writing up to 2/);assert.match(ignored,/show again/);
});
test('writers card: capped rows explain the gap, hosted helpers name their app, sandboxed traffic is reported apart',()=>{
 const wv={name:'msedgewebview2 (ultra suite)',self:true,level:null,avg_bps_1h:2e4,issued_bps_1h:4.1e6,capped:true,active_share_1h:1,bytes_today:1.7e9,per_day_estimate:1.7e9};
 const card=P.writersHtml({available:true,observed_minutes_1h:60,top:[wv],sandboxed_bps:3.4e5});
 assert.match(card,/msedgewebview2\.exe, used by ultra suite\.exe · this app/);assert.match(card,/Sandboxed browser helpers moved 340 KB\/s/);assert.match(card,/>Normal</);
 const d=P.writerDetail(wv);assert.match(d,/sent 4\.1 MB\/s of write requests, but the drives received less/);assert.match(d,/part of Ultra Suite/);assert.match(d,/up to 20 KB\/s/);
 const warn=P.writerSentence({...wv,self:false,level:'warning',avg_bps_1h:2.2e6,per_day_estimate:1.9e11,issued_bps_1h:9e6});assert.match(warn,/up to 2\.2 MB\/s/);assert.match(warn,/sent 9\.0 MB\/s of write requests/);
 assert.doesNotMatch(P.writerDetail({...wv,self:false,capped:false}),/part of Ultra Suite|traffic between programs, not drive writes/);
});
test('writer detail shows fixes with undo and sources, never an action button that changes Windows',()=>{
 const d=P.writerDetail(amd);assert.match(d,/Set-Service x/);assert.match(d,/Undo/);assert.match(d,/videocardz\.com/);assert.match(d,/Ignore this app/);assert.doesNotMatch(d,/data-wr-stop|Stop service/);
 const fixed_in={version:'2.1',status:'confirmed',source_url:'https://vendor.example/fix',confirmed_on:'2026-11-12'};
 const retired=P.writerDetail({...amd,known_issue:{...amd.known_issue,fixed_in,retired:true,installed_version:'2.1'}});assert.match(retired,/at or past the documented fix/);assert.doesNotMatch(retired,/likely a different cause|Set-Service x/);assert.match(retired,/vendor\.example\/fix/);assert.match(retired,/Sources:/);
 assert.match(P.writerDetail({...amd,known_issue:null,name:'logger'}),/never stops programs/);
});
test('AMD reported target retains advisory, date and investigation guidance even past its version',()=>{
 const entry=require('../pc_sentinel/backend/known_writers.json').entries[0];
 for(const installed_version of [null,'26.11.1','27.1.1']){
  // Also reject a stale or contradictory retired flag from an older backend.
  const row={...amd,known_issue:{...entry,installed_version,retired:true}};
  const html=P.writerDetail(row);
  assert.match(html,/Candidate fix: 26\.11\.1 is an unconfirmed reported target/);
  assert.match(html,/PA-365/);assert.match(html,/2026-11-12/);assert.match(html,/amd\.com\/en\/resources\/support-articles\/faqs\/PA-365\.html/);
  assert.match(html,/under development/);assert.match(html,/before considering a workaround/);
  assert.doesNotMatch(html,/Vendor-confirmed fix|likely a different cause|official fix|once a second|Set-Service/);
  assert.match(P.writerSentence(row),/does not establish/);
 }
 const quiet=P.writersHtml({available:true,observed_minutes_1h:60,top:[{...amd,level:null,avg_bps_1h:0,bytes_today:0}]});
 assert.match(quiet,/quiet sample does not rule out/);assert.match(quiet,/do not identify a destination drive/);
 assert.doesNotMatch(P.writerDetail({...amd,known_issue:{...entry,explanation:'<img src=x>',fixed_in:{version:'<script>',status:'reported_target'}}}),/<img src=x>|<script>/);
});
test('overview pill only for warning or heavy and not when ignored',()=>{
 assert.equal(P.overviewPill({flagged:[{...amd,level:'notice'}]}),'');
 assert.match(P.overviewPill({flagged:[amd]}),/Constant writes: AMD External Events Utility/);
 assert.equal(P.overviewPill({flagged:[amd]},['atieclxx']),'');
});
test('units',()=>{assert.equal(P.rate(650000),'650 KB/s');assert.equal(P.rate(5e6),'5.0 MB/s');assert.equal(P.bytes(4.3e11),'430.0 GB');assert.equal(P.rate(null),'–');});
test('unfair before/after pairs say why and make no claim',()=>{
 const before={ts:1,single_mbps:2000,multi_mbps:9000,wake_us:40,ac:true,background_load_pct:3};
 const after={ts:2,single_mbps:2600,multi_mbps:9000,wake_us:40,ac:false,background_load_pct:4};
 const html=P.benchHtml({before,after,compare:{single_mbps:{better_pct:30,noise_pct:3,real:false},not_comparable:['One run was on battery power and the other was plugged in.']}});
 assert.match(html,/be compared fairly/);assert.match(html,/on battery/);assert.match(html,/Not compared/);assert.doesNotMatch(html,/% better|% worse/);
 assert.match(html,/plugged in · 3% CPU busy before/);
});
