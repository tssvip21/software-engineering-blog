/* Local UI enhancement; records and permissions remain server-authoritative. */
(() => {
  'use strict';
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const parse=id=>{const el=document.getElementById(id);return el?JSON.parse(el.textContent):null;};
  const token=()=> $('[name="csrfmiddlewaretoken"]')?.value||'';
  const icon=(kind)=>{const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','2');svg.setAttribute('stroke-linecap','round');
    if(kind==='grip'){for(const x of [8,16])for(const y of [5,12,19]){const c=document.createElementNS(svg.namespaceURI,'circle');c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r','1');c.setAttribute('fill','currentColor');svg.append(c);}}
    else{const p=document.createElementNS(svg.namespaceURI,'path');p.setAttribute('d','m6 6 12 12M18 6 6 18');svg.append(p);}return svg;};
  async function api(url,body){const target=new URL(url,location.href);if(target.origin!==location.origin)throw new Error('요청 주소를 확인해 주세요.');const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);try{const response=await fetch(target,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':token(),'X-Taily-UI':'1'},...(body===undefined?{}:{body:JSON.stringify(body)})});const content=response.headers.get('Content-Type')||'';if(!content.includes('application/json'))throw Object.assign(new Error(response.status===403?'이 작업의 권한이 없어요. 로그인과 참여 상태를 확인해 주세요.':'응답을 확인하지 못했어요. 입력은 그대로예요.'),{status:response.status});const data=await response.json();if(!response.ok)throw Object.assign(new Error(data.message||'입력을 확인해 주세요.'),{status:response.status});return data;}finally{clearTimeout(timer);}}

  // Keep only this page's discovery choices when an inline record refreshes its content.
  const browseSelections=new Map();
  function setupBrowsing(){
    const info=parse('module-browse-data');if(!info)return;
    $$('[data-module-browser]').forEach(root=>{
      if(root.dataset.ready)return;root.dataset.ready='1';
      const scope=root.dataset.moduleBrowser,remembered=browseSelections.get(scope);
      let category=info.categories.some(c=>c.key===remembered?.category)?remembered.category:'all';
      let group=info.groups.some(g=>g.key===remembered?.group)?remembered.group:'all';
      const search=$('[data-module-search],#catalog-search',root),select=$('[data-custom-filter]',root);
      const memberships=Object.fromEntries([...info.categories,...info.groups].map(g=>[g.key,new Set(g.keys)]));
      if(search&&remembered)search.value=remembered.query;
      if(select)select.value=group;
      function filter(){const query=(search?.value||'').trim().toLocaleLowerCase('ko');let count=0;
        browseSelections.set(scope,{category,group,query:search?.value||''});
        $$('[data-module-key],[data-catalog-key]',root).forEach(row=>{const key=row.dataset.moduleKey||row.dataset.catalogKey;row.hidden=!((category==='all'||memberships[category]?.has(key))&&(group==='all'||memberships[group]?.has(key))&&row.dataset.search.toLocaleLowerCase('ko').includes(query));if(!row.hidden)count++;});
        $$('[data-module-section]',root).forEach(section=>{const n=$$('[data-module-key]:not([hidden])',section).length;section.hidden=n===0&&!(section.hasAttribute('data-empty-manage')&&(category==='all'||category===section.dataset.sectionCategory)&&group==='all'&&!query);section.dataset.visibleCount=String(n);const c=$('[data-section-count]',section);if(c)c.textContent=n;});
        const empty=$('[data-browser-empty]',root),counter=$('[data-browse-count]',root);if(empty)empty.hidden=count!==0;if(counter)counter.textContent=count+'개 기능';
        $$('[data-module-filter]',root).forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.moduleFilter===category)));
      }
      root.addEventListener('click',e=>{const b=e.target.closest('[data-module-filter]');if(b){category=b.dataset.moduleFilter;filter();}if(e.target.closest('[data-clear-browse]')){category=group='all';if(search)search.value='';if(select)select.value='all';filter();}});
      search?.addEventListener('input',filter);select?.addEventListener('change',()=>{group=select.value;filter();});filter();
    });
  }
  setupBrowsing();document.addEventListener('taily:refresh',setupBrowsing);

  // The native select remains a usable fallback when scripting is disabled.
  const catalogue=parse('breed-catalog'),breed=$('#id_breed_key'),species=$('#id_species');
  if(catalogue&&breed&&species){
    const specials=[['','나중에 선택'],['mixed','믹스 / Mixed breed'],['unknown','품종 미상 / Unknown'],['custom','목록에 없음 · 직접 입력']];
    const sex=$('#id_sex'),size=$('#id_size'),source=$('#id_size_origin'),free=$('#id_subspecies');
    const picker=document.createElement('div');picker.className='breed-picker';
    const input=document.createElement('input');input.type='text';input.id='breed-search';input.autocomplete='off';input.placeholder='한글·영문으로 품종 찾기';input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');input.setAttribute('aria-controls','breed-results');
    const results=document.createElement('div');results.id='breed-results';results.className='breed-results';results.setAttribute('role','listbox');results.setAttribute('aria-label','품종 검색 결과');results.hidden=true;
    picker.append(input,results);breed.after(picker);breed.hidden=true;const label=$(`label[for="${breed.id}"]`);if(label)label.htmlFor=input.id;
    const selected=document.createElement('p');selected.className='breed-selected';selected.setAttribute('aria-live','polite');picker.after(selected);
    const hint=document.createElement('p');hint.className='breed-notice';hint.setAttribute('aria-live','polite');size?.after(hint);
    let active=-1, sizePreference=source?.value||'auto';
    const chosen=()=>catalogue.find(b=>b.key===breed.value);
    const text=()=>chosen()?chosen().ko+' · '+chosen().en:(specials.find(p=>p[0]===breed.value)?.[1]||'나중에 선택');
    function close(){results.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');input.value=breed.value?text():'';}
    function setSize(userChange=false){const isDog=species.value==='dog';size.closest('.field').hidden=!isDog;source.closest('.field').hidden=!isDog;
      if(!isDog){size.value='';source.value='manual';hint.textContent='';return;}
      source.value=sizePreference;
      if(['mixed','unknown','custom',''].includes(breed.value)&&userChange)source.value=sizePreference='manual';
      const suggested=chosen()?.sizes[sex?.value||'']||'';
      if(source.value==='auto')size.value=suggested;
      hint.textContent=source.value==='manual'?'직접 고른 체격을 사용해요. 모르면 비워 두어도 돼요.':suggested?'품종 자료를 바탕으로 체격을 선택했어요. 우리 반려동물과 다르면 직접 바꿔 주세요.':'이 품종은 체격을 하나로 정하기 어려워요. 직접 고르거나 비워 두세요.';
    }
    function updateSelection(){selected.textContent='선택: '+text();free.closest('.field').hidden=Boolean(chosen());setSize(true);breed.dispatchEvent(new Event('change',{bubbles:true}));close();}
    function choose(key){breed.value=key;updateSelection();input.focus({preventScroll:true});close();}
    function render(query=''){
      const q=query.trim().toLocaleLowerCase('ko'),items=[...specials.map(([key,ko])=>({key,ko,en:''})),...catalogue.filter(b=>b.species===species.value)];
      const found=items.filter(b=>[b.ko,b.en,...(b.aliases||[])].join(' ').toLocaleLowerCase('ko').includes(q));results.replaceChildren();active=-1;
      found.forEach((b,i)=>{const option=document.createElement('button');option.type='button';option.className='breed-option';option.id='breed-option-'+i;option.tabIndex=-1;option.setAttribute('role','option');option.setAttribute('aria-selected',String(b.key===breed.value));option.dataset.breedKey=b.key;const ko=document.createElement('strong');ko.textContent=b.ko;option.append(ko);if(b.en){const en=document.createElement('small');en.textContent=b.en;option.append(en);}option.addEventListener('pointerdown',e=>e.preventDefault());option.addEventListener('click',()=>choose(b.key));results.append(option);});
      if(!found.length){const p=document.createElement('p');p.className='breed-notice';p.textContent='일치하는 품종이 없어요. 검색을 지우고 ‘목록에 없음’을 선택할 수 있어요.';results.append(p);}
      results.hidden=false;input.setAttribute('aria-expanded','true');
    }
    function syncSpecies(changed=false){const current=breed.value,b=chosen(),pet=['dog','cat'].includes(species.value);breed.closest('.field').hidden=!pet;
      breed.replaceChildren();for(const [value,name] of specials)breed.add(new Option(name,value));catalogue.filter(b=>b.species===species.value).forEach(b=>breed.add(new Option(b.ko+' · '+b.en,b.key)));
      breed.value=pet&&(!b||b.species===species.value)?current:'';if(!pet)breed.value='';
      if(changed&&b&&b.species!==species.value)free.value='';
      selected.textContent='선택: '+text();free.closest('.field').hidden=Boolean(chosen());setSize(false);close();
    }
    input.addEventListener('focus',()=>{render('');input.select();});input.addEventListener('input',()=>render(input.value));
    input.addEventListener('keydown',e=>{const options=$$('.breed-option',results);if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if(results.hidden)render('');const opts=$$('.breed-option',results);if(!opts.length)return;active=(active+(e.key==='ArrowDown'?1:-1)+opts.length)%opts.length;opts.forEach((o,i)=>o.setAttribute('aria-selected',String(i===active)));input.setAttribute('aria-activedescendant',opts[active].id);opts[active].scrollIntoView({block:'nearest'});}if(e.key==='Enter'&&!results.hidden){e.preventDefault();const option=options[active<0?0:active];if(option)choose(option.dataset.breedKey);}if(e.key==='Escape'){e.preventDefault();close();}});
    document.addEventListener('pointerdown',e=>{if(!picker.contains(e.target))close();});input.addEventListener('blur',()=>setTimeout(()=>{if(!picker.contains(document.activeElement))close();},0));
    species.addEventListener('change',()=>syncSpecies(true));sex?.addEventListener('change',()=>setSize());source?.addEventListener('change',()=>{sizePreference=source.value;setSize();});size?.addEventListener('change',()=>{source.value=sizePreference='manual';setSize();});syncSpecies();
  }

  // Native details preserve keyboard operation; only an authorized owner's menus exist in HTML.
  document.addEventListener('toggle',e=>{const menu=e.target;if(!menu.matches?.('.member-menu')||!menu.open)return;$$('.member-menu[open]').filter(x=>x!==menu).forEach(x=>x.open=false);menu.removeAttribute('data-drop-up');const box=$('.member-menu-body',menu)?.getBoundingClientRect();if(box&&box.bottom>innerHeight-16)menu.dataset.dropUp='1';},true);
  document.addEventListener('click',e=>{if(!e.target.closest('.member-menu'))$$('.member-menu[open]').forEach(x=>x.open=false);});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')$$('.member-menu[open]').forEach(x=>{x.open=false;$('summary',x)?.focus();});});
  document.addEventListener('submit',async e=>{const form=e.target;if(!form.matches('[data-member-action]'))return;e.preventDefault();if(form.dataset.busy)return;
    if(form.dataset.removePerson&&!confirm(form.dataset.removePerson+' 님을 내보낼까요? 작성한 기록은 유지돼요.'))return;
    form.dataset.busy='1';const button=$('button',form);button.disabled=true;const status=$('[data-member-status]',form.closest('.member-menu'));
    try{const response=await fetch(form.getAttribute('action'),{method:'POST',credentials:'same-origin',headers:{'X-Taily-UI':'1','X-CSRFToken':token()},body:new FormData(form)});const json=(response.headers.get('Content-Type')||'').includes('application/json')?await response.json():null;if(!response.ok||!json?.ok)throw new Error(json?.message||'변경할 권한이 없거나 참여 상태가 바뀌었어요.');status.textContent=json.message;location.reload();}catch(error){status.textContent=error.message;}finally{delete form.dataset.busy;button.disabled=false;}
  });

  const editor=$('#group-editor');let groupDirty=false;
  if(editor){
    const list=$('#group-list'),status=$('#group-status'),reload=$('[data-group-reload]');
    let state=parse('group-state'),version=state.version,saving=false,drag=null,keyboard=null;
    const modules=parse('group-modules');
    const rows=()=>$$('.group-editor-row',list);
    const current=()=>rows().map(row=>({id:row.dataset.id,name:$('[data-group-name]',row).value.trim(),keys:$$('[data-group-key]:checked',row).map(x=>x.dataset.groupKey)}));
    const equal=(a,b)=>JSON.stringify(a.map(g=>({...g,keys:[...g.keys].sort()})))===JSON.stringify(b.map(g=>({...g,keys:[...g.keys].sort()})));
    function announce(text,error=false){status.textContent=text;status.classList.toggle('form-errors',error);editor.dataset.status=error?'error':groupDirty?'dirty':'saved';}
    function changed(){groupDirty=!equal(current(),state.groups);$('#groups-empty').hidden=rows().length>0;rows().forEach((row,index)=>{$('.group-drag-handle',row).setAttribute('aria-label',`${index+1}번째 그룹 순서 이동`);$('.group-summary',row).textContent=$$('[data-group-key]:checked',row).map(x=>modules.find(m=>m.key===x.dataset.groupKey)?.name||'').join(' · ')||'이 그룹에 선택한 기능이 없어요.';});announce(groupDirty?'변경한 그룹을 저장해 주세요.':'저장된 그룹이에요.');}
    function rowFor(g,open=false){const row=document.createElement('section');row.className='group-editor-row';row.dataset.id=g.id;const header=document.createElement('div');header.className='group-row-heading';
      const handle=document.createElement('button');handle.type='button';handle.className='drag-handle group-drag-handle icon-button';handle.title='잡아서 이동 · Space로 선택, 방향키로 이동';handle.append(icon('grip'));
      const name=document.createElement('input');name.type='text';name.maxLength=40;name.required=true;name.value=g.name;name.dataset.groupName='';name.setAttribute('aria-label','그룹 이름');name.placeholder='그룹 이름';
      const remove=document.createElement('button');remove.type='button';remove.className='icon-button remove-group';remove.dataset.removeGroup='';remove.setAttribute('aria-label','이 그룹 삭제');remove.title='이 그룹만 삭제 · 기능과 기록은 유지';remove.append(icon('close'));header.append(handle,name,remove);row.append(header);
      const summaryText=document.createElement('p');summaryText.className='group-summary';row.append(summaryText);const details=document.createElement('details');details.open=open;const summary=document.createElement('summary');summary.textContent='기능 선택·변경';details.append(summary);
      const choices=document.createElement('fieldset');choices.className='group-selection';const legend=document.createElement('legend');legend.className='sr-only';legend.textContent='그룹에 넣을 기능';choices.append(legend);
      modules.forEach(m=>{const label=document.createElement('label'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.dataset.groupKey=m.key;checkbox.checked=g.keys.includes(m.key);label.append(checkbox,document.createTextNode(m.name));choices.append(label);});details.append(choices);row.append(details);return row;
    }
    function render(data){list.replaceChildren(...data.groups.map(g=>rowFor(g)));state=data;version=data.version;groupDirty=false;reload.hidden=true;changed();}
    render(state);
    editor.addEventListener('input',changed);editor.addEventListener('change',changed);
    editor.addEventListener('click',e=>{if(drag||keyboard)return;if(e.target.closest('[data-group-add]')){if(rows().length>=20){announce('그룹은 최대 20개까지 만들 수 있어요.',true);return;}const row=rowFor({id:crypto.randomUUID(),name:'',keys:[]},true);list.append(row);changed();$('[data-group-name]',row).focus();}
      if(e.target.closest('[data-remove-group]')){const row=e.target.closest('.group-editor-row');if(!confirm('이 그룹을 삭제할까요? 사용 중인 기능과 돌봄 기록은 그대로 남아요.'))return;const next=row.nextElementSibling||row.previousElementSibling;row.remove();changed();(next?$('[data-group-name]',next):$('[data-group-add]')).focus();}});
    editor.addEventListener('submit',async e=>{e.preventDefault();if(saving||drag||keyboard)return;if(!editor.reportValidity())return;saving=true;const button=$('button[type=submit]',editor);button.disabled=true;const desired=current();announce('그룹을 저장하고 있어요…');
      try{const data=await api(editor.dataset.endpoint,{version,groups:desired});if(equal(current(),desired)){state=data;version=data.version;groupDirty=false;reload.hidden=true;announce('그룹을 저장했어요. 돌봄 기능에서 선택해 볼 수 있어요.');}else{state=data;version=data.version;changed();}}
      catch(error){if(!error.status){try{const server=await api(editor.dataset.endpoint);if(equal(server.groups,desired)){state=server;version=server.version;changed();announce('서버에 저장된 그룹을 확인했어요.');return;}}catch(_){} }
        announce(error.status===409?'다른 참여자가 그룹을 바꿨어요. 내 변경은 남아 있어요. 최신 그룹을 확인한 뒤 다시 편집해 주세요.':error.name==='AbortError'?'연결이 늦어지고 있어요. 입력은 유지돼요. 다시 저장해 주세요.':error.message,true);reload.hidden=false;
      }finally{saving=false;button.disabled=false;}});
    reload.addEventListener('click',async()=>{if(groupDirty&&!confirm('저장하지 않은 그룹 변경을 버리고 최신 그룹을 불러올까요?'))return;try{render(await api(editor.dataset.endpoint));}catch(error){announce(error.message,true);}});
    const order=()=>rows().map(r=>r.dataset.id);
    function restore(keys){keys.forEach(id=>{const row=rows().find(r=>r.dataset.id===id);if(row)list.append(row);});}
    function move(row,before){if(row===before||row.nextElementSibling===before)return;list.insertBefore(row,before);}
    function endDrag(cancel=false){if(!drag)return;const d=drag;drag=null;cancelAnimationFrame(d.frame);d.row.classList.remove('is-picked');document.body.classList.remove('group-dragging');if(cancel)restore(d.before);changed();d.handle.focus({preventScroll:true});}
    function tick(){if(!drag)return;if(drag.y<100)window.scrollBy(0,-10);else if(drag.y>innerHeight-80)window.scrollBy(0,10);drag.frame=requestAnimationFrame(tick);}
    list.addEventListener('pointerdown',e=>{const handle=e.target.closest('.group-drag-handle');if(!handle||e.button!==0||keyboard||saving)return;e.preventDefault();const row=handle.closest('.group-editor-row');drag={row,handle,before:order(),id:e.pointerId,y:e.clientY,frame:0};row.classList.add('is-picked');document.body.classList.add('group-dragging');drag.frame=requestAnimationFrame(tick);});
    window.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.id)return;e.preventDefault();drag.y=e.clientY;const target=rows().filter(r=>r!==drag.row).find(r=>{const b=r.getBoundingClientRect();return e.clientY<b.top+b.height/2;})||null;move(drag.row,target);},{passive:false});
    window.addEventListener('pointerup',e=>{if(drag&&e.pointerId===drag.id)endDrag();});window.addEventListener('pointercancel',()=>endDrag(true));window.addEventListener('blur',()=>endDrag(true));
    list.addEventListener('keydown',e=>{const handle=e.target.closest('.group-drag-handle');if(!handle)return;const row=handle.closest('.group-editor-row');
      if([' ','Enter'].includes(e.key)&&!keyboard){e.preventDefault();keyboard={row,before:order()};row.classList.add('is-picked');handle.setAttribute('aria-pressed','true');announce('방향키로 그룹을 옮기고 Enter로 놓아요. Escape로 되돌려요.');return;}
      if(!keyboard||keyboard.row!==row)return;
      if(['ArrowUp','ArrowDown','Home','End'].includes(e.key)){e.preventDefault();if(e.key==='ArrowUp'&&row.previousElementSibling)move(row,row.previousElementSibling);if(e.key==='ArrowDown'&&row.nextElementSibling)move(row,row.nextElementSibling.nextElementSibling);if(e.key==='Home'&&row!==list.firstElementChild)move(row,list.firstElementChild);if(e.key==='End')move(row,null);handle.focus({preventScroll:true});row.scrollIntoView({block:'nearest'});}
      if(['Enter',' ','Escape','Tab'].includes(e.key)){if(e.key!=='Tab')e.preventDefault();const original=keyboard.before;keyboard=null;row.classList.remove('is-picked');handle.removeAttribute('aria-pressed');if(e.key==='Escape')restore(original);changed();}
    });
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&drag){e.preventDefault();endDrag(true);}});
  }
  window.addEventListener('beforeunload',e=>{if(groupDirty){e.preventDefault();e.returnValue='';}});
})();
