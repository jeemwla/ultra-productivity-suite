/* Native, searchable cascading selects. No dependency or remote runtime. */
(function(root){
  'use strict';
  const sort = values => [...new Set(values)].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));
  const rangeKey = r => `range:${r.min_w}:${r.max_w}`;
  function wattOptions(records){
    const options=new Map();
    for(const r of records){
      for(const w of r.wattage.variants_w)options.set(String(w),`${w} W`);
      const span=r.wattage.range;if(span)options.set(rangeKey(span),`${span.min_w}–${span.max_w} W series (exact rating required)`);
    }
    return [...options].sort((a,b)=>a[0].localeCompare(b[0],undefined,{numeric:true}));
  }
  function matchesWatt(record, key){
    return !key || (key.startsWith('range:') ? !!record.wattage.range && rangeKey(record.wattage.range)===key : record.wattage.variants_w.includes(Number(key)));
  }
  function narrow(records,{brand='',watt='',size=''}={}){
    return records.filter(r=>(!brand||r.brand===brand)&&matchesWatt(r,watt)&&(!size||r.form_factor===size));
  }
  function modelLabel(r){
    return [[r.series,r.sub_series,r.variant].filter(Boolean).join(' '),r.intro_year,r.atx_version,r.platform].filter(Boolean).join(' · ');
  }
  function draft(record,watt){
    if(!matchesWatt(record,watt)||!watt)throw Error('Choose a listed wattage or range.');
    return {rated_watts:watt.startsWith('range:')?'':Number(watt),
      model:[record.brand,record.series,record.sub_series,record.variant].filter(Boolean).join(' ').slice(0,120),form_factor:record.form_factor};
  }
  const functions={wattOptions,matchesWatt,narrow,modelLabel,draft};
  if(typeof module==='object'&&module.exports)module.exports=functions;
  if(!root.document)return;
  const $=id=>document.getElementById(id);
  if(!$('psu-catalog'))return;
  let records=[],loaded=false,loading=false;
  function options(id,items,placeholder){
    const select=$(id);select.replaceChildren(new Option(placeholder,''));
    for(const [value,label] of items)select.add(new Option(label,value));
    select.disabled=!items.length;
  }
  function cascade(level){
    if(level===0){
      const query=$('psu-brand-search').value.trim().toLocaleLowerCase();
      options('psu-brand',sort(records.map(r=>r.brand)).filter(b=>b.toLocaleLowerCase().includes(query)).map(b=>[b,b]),'Choose brand');
    }
    const brand=$('psu-brand').value;
    if(level<=1)options('psu-watts-choice',brand?wattOptions(narrow(records,{brand})):[],'Choose wattage');
    const watt=$('psu-watts-choice').value;
    if(level<=2)options('psu-form-choice',brand&&watt?sort(narrow(records,{brand,watt}).map(r=>r.form_factor)).map(f=>[f,f||'Unspecified']):[],'Choose form factor');
    const size=$('psu-form-choice').value;
    if(level<=3)options('psu-model-choice',brand&&watt&&size?narrow(records,{brand,watt,size}).map(r=>[r.id,modelLabel(r)]):[],'Choose model / series');
    $('psu-use').disabled=!$('psu-model-choice').value;
    $('psu-choice-status').textContent='Selecting from the catalog does not save anything. Check the PSU label before saving.';
  }
  async function load(){
    if(loading||loaded)return;loading=true;$('psu-catalog-retry').disabled=true;
    $('psu-catalog-status').textContent='Loading the local catalog…';
    try{
      const response=await fetch('/api/power/catalog');if(!response.ok)throw Error('Catalog unavailable');
      const document=await response.json();records=document.records;
      if(!Array.isArray(records)||!records.length)throw Error('Catalog empty');
      loaded=true;cascade(0);
      $('psu-catalog-status').textContent=`${records.length.toLocaleString()} series from ${sort(records.map(r=>r.brand)).length} brands. ${document.storage==='local_cache'?'Local update':'Bundled offline copy'}, ${document.source.retrieved_utc.slice(0,10)}. Specifications may be incomplete; verify your label.`;
      $('psu-catalog-retry').hidden=true;
    }catch{ $('psu-catalog-status').textContent='The catalog could not be loaded. Enter your PSU details manually below, or retry.'; }
    finally{loading=false;$('psu-catalog-retry').disabled=false;}
  }
  $('psu-brand-search').addEventListener('input',()=>cascade(0));
  ['psu-brand','psu-watts-choice','psu-form-choice','psu-model-choice'].forEach((id,index)=>$(id).addEventListener('change',()=>cascade(index+1)));
  $('psu-catalog').addEventListener('toggle',()=>{if($('psu-catalog').open)load();});
  $('psu-catalog-retry').addEventListener('click',load);
  $('psu-use').addEventListener('click',()=>{
    const record=records.find(r=>r.id===$('psu-model-choice').value);if(!record)return;
    const value=draft(record,$('psu-watts-choice').value);
    for(const [id,key] of [['psu-w','rated_watts'],['psu-model','model'],['psu-size','form_factor']]){
      $(id).value=value[key];$(id).dispatchEvent(new Event('input',{bubbles:true}));
    }
    $('psu-choice-status').textContent=value.rated_watts===''?'Series selected. Enter the exact rated wattage from your PSU label below before saving.':'Details filled in below. Check them, then choose Save PSU details.';
    if(record.wattage.uncertain)$('psu-choice-status').textContent+=' The source marks this wattage as uncertain.';
    $('psu-w').focus();
  });
})(typeof window==='object'?window:globalThis);
