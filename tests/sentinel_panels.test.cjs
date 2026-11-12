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
 assert.match(quiet,/No process is writing continuously/);assert.match(quiet,/<th scope="col">Program<\/th>/);assert.match(quiet,/Normal/);
 assert.match(P.writersHtml({available:true,observed_minutes_1h:12,top:[]}),/12 of the 60 minutes/);
 const hit=P.writersHtml({available:true,observed_minutes_1h:60,top:[amd],drive_write_bps:3e6});
 assert.match(hit,/AMD External Events Utility is writing 2\.5 MB\/s almost all the time \(98% of the last hour\), a known issue/);assert.match(hit,/216\.0 GB a day/);assert.match(hit,/>Warning</);assert.match(hit,/All drives together: 3\.0 MB\/s/);assert.match(hit,/not flash wear/);
 const ignored=P.writersHtml({available:true,observed_minutes_1h:60,top:[amd]},['atieclxx']);assert.doesNotMatch(ignored,/is writing 2/);assert.match(ignored,/show again/);
});
test('writer detail shows fixes with undo and sources, never an action button that changes Windows',()=>{
 const d=P.writerDetail(amd);assert.match(d,/Set-Service x/);assert.match(d,/Undo/);assert.match(d,/videocardz\.com/);assert.match(d,/Ignore this app/);assert.doesNotMatch(d,/data-wr-stop|Stop service/);
 const retired=P.writerDetail({...amd,known_issue:{...amd.known_issue,retired:true,installed_version:'26.11.1'}});assert.match(retired,/likely a different cause/);assert.doesNotMatch(retired,/Set-Service x/);
 assert.match(P.writerDetail({...amd,known_issue:null,name:'logger'}),/never stops programs/);
});
test('overview pill only for warning or heavy and not when ignored',()=>{
 assert.equal(P.overviewPill({flagged:[{...amd,level:'notice'}]}),'');
 assert.match(P.overviewPill({flagged:[amd]}),/Constant writes: AMD External Events Utility/);
 assert.equal(P.overviewPill({flagged:[amd]},['atieclxx']),'');
});
test('units',()=>{assert.equal(P.rate(650000),'650 KB/s');assert.equal(P.rate(5e6),'5.0 MB/s');assert.equal(P.bytes(4.3e11),'430.0 GB');assert.equal(P.rate(null),'–');});
