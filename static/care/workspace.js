/* Shared panel and form behaviour. Existing save services remain authoritative. */
(() => {
  'use strict';
  const wide = matchMedia('(min-width: 1200px)');
  const choices = new Map();
  let scheduleFrame=0;
  function fitScheduleNow() {
    const rail=document.querySelector('.schedule-rail');
    const box=rail?.querySelector('[data-schedule-panel]');
    if(!box)return;
    if(!wide.matches){
      box.classList.remove('is-fixed');
      for(const key of ['top','left','width','height','heading'])box.style.removeProperty('--schedule-'+key);
      rail.style.removeProperty('min-height');
      return;
    }
    const workspace=rail.closest('.care-workspace').getBoundingClientRect();
    const bounds=rail.getBoundingClientRect();
    const header=document.querySelector('.workspace-topbar')?.getBoundingClientRect();
    const top=Math.max(workspace.top,(header?.bottom||0)+16);
    box.style.setProperty('--schedule-top',top+'px');
    box.style.setProperty('--schedule-left',bounds.left+'px');
    box.style.setProperty('--schedule-width',bounds.width+'px');
    box.style.setProperty('--schedule-height',Math.max(80,innerHeight-top-16)+'px');
    box.classList.add('is-fixed');
    const heading=box.querySelector('summary').getBoundingClientRect().height;
    box.style.setProperty('--schedule-heading',heading+'px');
    rail.style.minHeight=(heading+2)+'px';
  }
  function fitSchedule() {
    cancelAnimationFrame(scheduleFrame);
    scheduleFrame=requestAnimationFrame(fitScheduleNow);
  }
  const scheduleObserver=new ResizeObserver(fitSchedule);
  window.addEventListener('scroll',fitSchedule,{passive:true});
  window.addEventListener('resize',fitSchedule);
  function panel() {
    const node=document.querySelector('[data-schedule-panel]');
    if(!node || node.dataset.initialized)return;
    node.dataset.initialized='1';
    scheduleObserver.disconnect();
    scheduleObserver.observe(node.closest('.care-workspace'));
    const header=document.querySelector('.workspace-topbar');if(header)scheduleObserver.observe(header);
    fitSchedule();
    const remembered=choices.get(location.pathname+':'+wide.matches);
    node.open=remembered===undefined ? wide.matches : remembered;
    node.addEventListener('toggle',()=>{choices.set(location.pathname+':'+wide.matches,node.open);fitSchedule();});
  }
  wide.addEventListener('change',()=>{
    const node=document.querySelector('[data-schedule-panel]');if(!node)return;
    node.open=choices.get(location.pathname+':'+wide.matches) ?? wide.matches;
    fitSchedule();
  });
  function petForm(form) {
    if(form.dataset.petReady)return;form.dataset.petReady='1';
    const data=form.querySelector('#pet-breeds-data');if(!data)return;
    let breeds;try{breeds=JSON.parse(data.textContent);}catch(_){return;}
    const get=name=>form.querySelector('[name="'+name+'"]');
    const species=get('species'),breed=get('breed_key'),sex=get('sex'),maturity=get('maturity'),size=get('size'),source=get('size_origin'),custom=get('subspecies');
    if(!species||!breed||!size||!source)return;
    const hint=form.querySelector('[data-size-suggestion]');
    const field=input=>input?.closest('[data-field]');
    const specials=[['','나중에 선택'],['mixed','믹스'],['unknown','품종 미상'],['custom','목록에 없음 · 직접 입력']];
    const names={small:'소형',medium:'중형',large:'대형'};
    function sizing(changed=false) {
      const dog=species.value==='dog';if(field(size))field(size).hidden=!dog;
      const chosen=breeds.find(x=>x.key===breed.value && x.species===species.value);
      const suggestion=dog&&maturity?.value==='adult' ? chosen?.sizes[sex?.value||'']||'' : '';
      if(!dog){size.value='';source.value='manual';}
      else if(changed){if(suggestion){size.value=suggestion;source.value='auto';}else{source.value='manual';}}
      if(hint){hint.textContent=!dog?'':suggestion ? (source.value==='auto'?'품종 자료로 '+names[suggestion]+'을 제안했어요. 직접 바꿀 수 있어요.':'품종 자료의 제안은 '+names[suggestion]+'이에요. 현재 선택한 값은 유지해요.') : maturity?.value==='growing'?'성장 중에는 직접 고르거나 모름으로 두세요.':'직접 고르거나 모름으로 두세요.';}
      if(field(custom))field(custom).hidden=Boolean(chosen);
    }
    function kinds(changed=false) {
      const old=breed.value,valid=breeds.find(x=>x.key===old);
      const compatible=!valid||valid.species===species.value;
      breed.replaceChildren(...specials.map(([v,t])=>new Option(t,v)),...breeds.filter(x=>x.species===species.value).map(x=>new Option(x.ko+' · '+x.en,x.key)));
      breed.value=compatible ? old : '';
      if(field(breed))field(breed).hidden=!['dog','cat'].includes(species.value);
      if(!['dog','cat'].includes(species.value))breed.value='';
      if(changed&&!compatible&&custom)custom.value='';
      sizing(changed);
    }
    species.addEventListener('change',()=>kinds(true));breed.addEventListener('change',()=>sizing(true));
    sex?.addEventListener('change',()=>sizing(true));maturity?.addEventListener('change',()=>sizing(true));
    size.addEventListener('change',()=>{source.value='manual';sizing(false);});
    kinds(false);
  }
  function suppliesForm(form) {
    if(form.dataset.supplyReady)return;form.dataset.supplyReady='1';
    const kind=form.querySelector('[name="category"]'),track=form.querySelector('[name="track_stock"]');if(!kind||!track)return;
    const nutrition=['energy','energy_basis','grams_per_piece','protein','protein_kind','fat','fat_kind','source'];
    function toggle(){
      const food=['meal','treat','both'].includes(kind.value);
      nutrition.forEach(key=>{const f=form.querySelector('[data-field="'+key+'"]');if(!f)return;f.hidden=!food;f.querySelectorAll('input,select,textarea').forEach(e=>{e.disabled=!food;});});
      const header=form.querySelector('[data-nutrition-section]');if(header)header.hidden=!food;
      ['stock_item','stock_unit'].forEach(key=>{const f=form.querySelector('[data-field="'+key+'"]');if(f){f.hidden=!track.checked;f.querySelectorAll('input,select').forEach(e=>e.disabled=!track.checked);}});
      if(track.checked){const unit=form.querySelector('[name="stock_unit"]');if(unit&&!unit.value)unit.value=food?'g':'piece';}
    }
    kind.addEventListener('change',toggle);track.addEventListener('change',toggle);toggle();
  }
  function recordProduct(select) {
    if(select.dataset.productReady)return;select.dataset.productReady='1';
    const form=select.closest('form');if(!form)return;
    let options;try{options=JSON.parse(select.dataset.supplyOptions);}catch(_){return;}
    let lastName='';
    function choose(changed=false){
      const product=options.find(x=>x.id===select.value);if(!product){lastName='';return;}
      if(select.dataset.supplyModule==='inventory'){
        const item=form.querySelector('[name="item"]'),quantity=form.querySelector('[name="quantity"]'),unit=form.querySelector('[name="unit"]');
        if(item&&(changed||!item.value))item.value=product.stock_item;
        const pack=product.package,version=form.querySelector('[name="package_version"]');
        if(version&&changed)version.value=pack?.version||'';
        const option=unit?.querySelector('option[value="package"]');
        if(option){option.textContent=pack ? pack.label+' (등록한 포장 용량)' : '포장 개수 (용량 등록 필요)';option.disabled=!pack;}

        if(unit&&quantity&&!quantity.value&&product.stock_unit)unit.value=product.stock_unit;
      } else {
        const title=form.querySelector('[name="routine_title"]'),amount=form.querySelector('[name="used_quantity"]'),unit=form.querySelector('[name="used_unit"]');
        if(title&&(!title.value||title.value===lastName))title.value=product.name;
        if(unit&&amount&&!amount.value&&product.stock_unit)unit.value=product.stock_unit;
        const countOption=unit?.querySelector('option[value="piece"]');if(countOption)countOption.textContent=product.package?.unit==='piece'?'개':product.package?.label||'개';
      }
      lastName=product.name;
    }
    select.addEventListener('change',()=>choose(true));choose(false);
  }
  function forms(root=document) {
    root.querySelectorAll('[data-pet-profile]').forEach(petForm);
    root.querySelectorAll('[data-supply-editor]').forEach(suppliesForm);
    root.querySelectorAll('select[data-supply-options]').forEach(recordProduct);
  }
  let viewportFrame=0;
  function fitDialogNow() {
    const d=document.querySelector('#input-dialog[open]');if(!d)return;
    const v=window.visualViewport;
    const h=v&&v.scale===1?v.height:window.innerHeight;
    const top=v&&v.scale===1?v.offsetTop:0;
    const available=Math.max(180,h-20);
    d.style.setProperty('--available-dialog-height',available+'px');
    // Measure after constraining the new form; do not retain the previous form's vertical offset.
    const box=d.getBoundingClientRect();
    d.style.setProperty('--dialog-top',top+Math.max(8,(h-box.height)/2)+'px');
  }
  function fitDialog() {
    fitDialogNow();
    cancelAnimationFrame(viewportFrame);
    viewportFrame=requestAnimationFrame(fitDialogNow);
  }
  const dialog=document.querySelector('#input-dialog');
  if(dialog){
    new ResizeObserver(fitDialog).observe(dialog);
    new MutationObserver(fitDialog).observe(dialog,{attributes:true,attributeFilter:['open']});
  }
  window.visualViewport?.addEventListener('resize',fitDialog);
  window.visualViewport?.addEventListener('scroll',fitDialog);
  window.addEventListener('resize',fitDialog);
  document.addEventListener('taily:input-ready',event=>{forms(event.target);fitDialog();});
  document.addEventListener('taily:refresh',()=>{panel();forms();});
  panel();forms();fitDialog();
})();
