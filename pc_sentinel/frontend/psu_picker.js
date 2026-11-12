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
  /* Smart search: words in any order, matched across brand, series, sub-series, variant, form factor, ATX version,
     year, platform and ODM; numbers match exact wattage options (or fall inside a series range, ranked lower).
     Prefixes and one-letter typos still match. Every word must match something. */
  const fold = s => String(s||'').normalize('NFKD').replace(/[̀-ͯ]/g,'').toLowerCase();
  const words = s => fold(s).split(/[^a-z0-9.+]+/).filter(Boolean);
  function near(a,b){ // edit distance <= 1, including one swap of neighbouring letters
    if(a===b)return true;if(Math.abs(a.length-b.length)>1)return false;
    let i=0;while(i<a.length&&i<b.length&&a[i]===b[i])i++;
    if(a.length===b.length)return a.slice(i+1)===b.slice(i+1)||(a[i]===b[i+1]&&a[i+1]===b[i]&&a.slice(i+2)===b.slice(i+2));
    return a.length>b.length?a.slice(i+1)===b.slice(i):a.slice(i)===b.slice(i+1);
  }
  const indexCache=new WeakMap();
  function indexOf(r){
    let ix=indexCache.get(r);if(ix)return ix;
    const brand=words(r.brand),name=words([r.series,r.sub_series,r.variant].join(' '));
    const other=words([r.form_factor,r.atx_version,(r.atx_version||'').replace(/\s+/g,''),r.intro_year,r.platform,r.modularity].join(' ')); // not the ODM: "seasonic" should mean the brand
    ix={brand,name,all:[...brand,...name,...other],form:fold(r.form_factor)};indexCache.set(r,ix);return ix;
  }
  function scoreWord(token,ix){
    let best=0;
    for(const w of ix.all){
      const s=w===token?3:w.startsWith(token)&&(token.length>=2||/^\d$/.test(token))?2:token.length>=4&&w.length>=4&&near(token,w)?1:0;
      if(s>best)best=s;if(best===3)break;
    }
    if(best&&ix.brand.some(w=>w===token||w.startsWith(token)))best+=1; // brand words rank first
    if(token===ix.form)best+=1;                                      // "sfx" prefers SFX over SFX-L
    return best;
  }
  function search(list,query,limit=10){
    const tokens=words(query).map(t=>t.replace(/^(\d{2,4})w$/,'$1'));
    if(!tokens.length)return [];
    const out=[];
    for(const r of list){
      const ix=indexOf(r);let score=0,watt='';
      for(const t of tokens){
        if(/^\d{2,4}$/.test(t)&&!(t.length===4&&/^(19|20)\d\d$/.test(t)&&String(r.intro_year)===t)){
          const n=Number(t);
          if(r.wattage.variants_w.includes(n)){score+=3;watt=String(n);continue;}
          if(r.wattage.range&&n>=r.wattage.range.min_w&&n<=r.wattage.range.max_w){score+=1;watt=rangeKey(r.wattage.range);continue;}
          const y=scoreWord(t,ix);if(y){score+=y;continue;}
          score=-1;break;
        }
        const s=scoreWord(t,ix);if(!s){score=-1;break;}score+=s;
      }
      if(score<0)continue;
      if(!watt&&r.wattage.variants_w.length===1)watt=String(r.wattage.variants_w[0]);
      if(!watt&&!r.wattage.variants_w.length&&r.wattage.range)watt=rangeKey(r.wattage.range);
      out.push({record:r,score,watt});
    }
    out.sort((a,b)=>b.score-a.score||a.record.brand.localeCompare(b.record.brand)||(Number(b.record.intro_year)||0)-(Number(a.record.intro_year)||0)||modelLabel(a.record).localeCompare(modelLabel(b.record)));
    return out.slice(0,limit);
  }
  function wattLabel(r){
    const v=r.wattage.variants_w,span=r.wattage.range;
    return v.length?v.join(' / ')+' W':span?`${span.min_w}–${span.max_w} W series`:'';
  }
  const functions={wattOptions,matchesWatt,narrow,modelLabel,draft,search,wattLabel};
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
    if(level===0)options('psu-brand',sort(records.map(r=>r.brand)).map(b=>[b,b]),'Choose brand');
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
      if($('psu-brand-search').value.trim())$('psu-brand-search').dispatchEvent(new Event('input'));
    }catch{ $('psu-catalog-status').textContent='The catalog could not be loaded. Enter your PSU details manually below, or retry.'; }
    finally{loading=false;$('psu-catalog-retry').disabled=false;}
  }
  /* ---- smart search box with live suggestions (combobox pattern) ---- */
  const box=$('psu-brand-search'),list=$('psu-suggest');
  let hits=[],active=-1,timer=0;
  function closeList(){list.hidden=true;box.setAttribute('aria-expanded','false');box.removeAttribute('aria-activedescendant');active=-1;}
  function mark(text,tokens){
    const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    let html=esc(text);
    for(const t of tokens.filter(t=>t.length>=2&&!/^\d+$/.test(t)).sort((a,b)=>b.length-a.length))
      html=html.replace(new RegExp(`(^|[^a-z0-9])(${t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`,'gi'),'$1<mark>$2</mark>');
    return html;
  }
  function render(){
    const q=box.value.trim();
    if(!loaded||!q){closeList();$('psu-choice-status').textContent=loaded?'':$('psu-choice-status').textContent;return;}
    hits=search(records,q,10);const tokens=words(q);
    list.replaceChildren(...hits.map((h,i)=>{
      const r=h.record,li=document.createElement('li');
      li.id='psu-opt-'+i;li.setAttribute('role','option');li.setAttribute('aria-selected',String(i===active));
      li.innerHTML=`<span class="psu-opt-name">${mark([r.brand,r.series,r.sub_series,r.variant].filter(Boolean).join(' '),tokens)}</span>`+
        `<span class="psu-opt-meta">${mark([wattLabel(r),r.form_factor,r.atx_version,r.intro_year,r.modularity&&r.modularity!=='No'?r.modularity+' modular':r.modularity==='No'?'Non-modular':''].filter(Boolean).join(' · '),tokens)}</span>`;
      li.addEventListener('mousedown',e=>{e.preventDefault();pick(i);});
      return li;
    }));
    list.hidden=!hits.length;box.setAttribute('aria-expanded',String(!!hits.length));
    $('psu-choice-status').textContent=hits.length?`${hits.length===10?'Top 10':hits.length} match${hits.length===1?'':'es'}. Use the arrow keys and Enter, or click one.`:'No match. Try fewer words, a brand, a wattage such as 850, or a size such as SFX.';
  }
  function setActive(i){
    active=(i+hits.length)%hits.length;
    [...list.children].forEach((li,k)=>li.setAttribute('aria-selected',String(k===active)));
    box.setAttribute('aria-activedescendant','psu-opt-'+active);list.children[active]?.scrollIntoView({block:'nearest'});
  }
  function pick(i){
    const h=hits[i];if(!h)return;const r=h.record;
    $('psu-brand').value=r.brand;cascade(1);
    if(h.watt&&[...$('psu-watts-choice').options].some(o=>o.value===h.watt)){$('psu-watts-choice').value=h.watt;cascade(2);}
    if($('psu-watts-choice').value){$('psu-form-choice').value=r.form_factor;cascade(3);$('psu-model-choice').value=r.id;cascade(4);}
    // Fill the form without clearing anything the user already typed: only fields this choice determines change.
    const fills=[['psu-model',[r.brand,r.series,r.sub_series,r.variant].filter(Boolean).join(' ').slice(0,120)],['psu-size',r.form_factor]];
    if(h.watt&&!h.watt.startsWith('range:'))fills.push(['psu-w',h.watt]);
    for(const [id,v] of fills){$(id).value=v;$(id).dispatchEvent(new Event('input',{bubbles:true}));}
    box.value=[r.brand,r.series,r.sub_series,r.variant].filter(Boolean).join(' ');closeList();
    $('psu-choice-status').textContent=h.watt&&!h.watt.startsWith('range:')?'Filled in below. Check the label on your PSU, then choose Save PSU details.':
      r.wattage.variants_w.length>1?`Filled in below. This series comes in ${wattLabel(r)}: choose the wattage above or type it below, then save.`:
      'Filled in below. This is a series range: type the exact rated wattage from your PSU label, then save.';
    if(r.wattage.uncertain)$('psu-choice-status').textContent+=' The source marks this wattage as uncertain.';
  }
  box.addEventListener('focus',load);
  box.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{active=-1;render();},60);});
  box.addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'&&hits.length&&!list.hidden){e.preventDefault();setActive(active+1);}
    else if(e.key==='ArrowUp'&&hits.length&&!list.hidden){e.preventDefault();setActive(active-1);}
    else if(e.key==='Enter'&&!list.hidden&&hits.length){e.preventDefault();pick(active<0?0:active);}
    else if(e.key==='Escape'&&!list.hidden){e.preventDefault();closeList();}
  });
  box.addEventListener('blur',()=>setTimeout(closeList,120));
  ['psu-brand','psu-watts-choice','psu-form-choice','psu-model-choice'].forEach((id,index)=>$(id).addEventListener('change',()=>cascade(index+1)));
  $('psu-catalog').addEventListener('toggle',()=>{if($('psu-catalog').open)load();});
  $('psu-catalog-retry').addEventListener('click',load);
  if($('psu-catalog').open)load();
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
