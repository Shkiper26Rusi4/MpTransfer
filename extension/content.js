(() => {
  globalThis.__MTphotoBuffers ||= new Map();
  if (globalThis.__marketplaceTransferInspectorV2) chrome.runtime.onMessage.removeListener(globalThis.__marketplaceTransferInspectorV2);
  const visible = el => Boolean(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
  const text = el => (el.innerText || el.textContent || '').trim().slice(0,300);
  function paginationNext() {
    return [...document.querySelectorAll('a,button')].filter(el=>{
      if(!visible(el) || el.disabled || el.getAttribute('aria-disabled')==='true') return false;
      let parent=el,inside=false;
      for(let i=0;parent && i<6;i++,parent=parent.parentElement) if(/pagination/i.test(parent.getAttribute('data-marker') || '')) inside=true;
      const semantics=[el.getAttribute('rel'),el.getAttribute('data-marker'),el.getAttribute('aria-label'),el.getAttribute('title'),text(el)].join(' ');
      if(el.tagName==='A' && (el.getAttribute('aria-label') || text(el)).trim()==='Следующая страница') {
        try {const target=new URL(el.href);inside ||= target.origin===location.origin && target.pathname===location.pathname && (location.pathname==='/profile' || location.pathname.startsWith('/profile/items/'));} catch {}
      }
      return inside && /next|следующ|далее/i.test(semantics);
    });
  }
  function inspect() {
    const elements = [...document.querySelectorAll('a,button,input,textarea,select,[role="tab"],[role="button"],[role="option"]')].filter(visible);
    const controls = elements.slice(0,2000).filter(el => !['password','hidden'].includes(el.type)).map(el => {
      const labels = el.labels ? [...el.labels].map(text).join(' ') : '';
      const out = {tag:el.tagName.toLowerCase(),type:el.getAttribute('type') || '',role:el.getAttribute('role') || '',
        label:(el.getAttribute('aria-label') || labels || text(el) || el.getAttribute('placeholder') || '').slice(0,300),
        marker:el.getAttribute('data-marker') || '',name:el.getAttribute('name') || '',required:el.required === true,disabled:el.disabled === true,selected:el.getAttribute('aria-selected')};
      if (el.tagName === 'A') {
        try { const u=new URL(el.href);if(u.origin===location.origin) out.path=u.pathname; } catch {}
      }
      if(['radio','checkbox'].includes(el.type)) out.checked=el.checked;
      if(el.type==='file') {out.multiple=el.multiple;out.accept=el.accept;}
      if(location.pathname==='/additem' && (['title','description'].includes(el.name) || ['price','params[199748]/input'].includes(out.marker))) out.value=el.value;
      if (el.tagName === 'SELECT') out.options=[...el.options].map(o=>({label:o.text,value:o.value}));
      return out;
    });
    const headings=[...document.querySelectorAll('h1,h2,h3,[role="heading"]')].filter(visible).slice(0,60).map(text);
    const tabLabels=[...document.querySelectorAll('[role="tab"]')].filter(visible).map(text);
    const sample=headings.join(' ')+' '+controls.map(c=>c.label).join(' ');
    const challenge=/captcha|капч|подтвердите,? что вы|проверка безопасности|доступ ограничен/i.test(sample);
    const markerElements=[...document.querySelectorAll('[data-marker]')].filter(visible);
    const markers=markerElements.slice(0,2500).map(el=>({marker:el.getAttribute('data-marker'),tag:el.tagName.toLowerCase(),text:text(el)}));
    const listingLinks=[...document.querySelectorAll('a[href]')].filter(visible).map(el=>{
      try {const u=new URL(el.href);const id=u.pathname.match(/_(\d{8,})$/)?.[1];return u.origin===location.origin && id ? {id,title:text(el),path:u.pathname}:null;}catch{return null;}
    }).filter(x=>x && x.title);
    const ancestors=listingLinks.slice(0,3).map(item=>{
      const link=[...document.querySelectorAll('a[href]')].find(el=>el.href.endsWith(item.path) && text(el));
      let node=link;const chain=[];
      for(let i=0;node && i<6;i++,node=node.parentElement) chain.push({tag:node.tagName.toLowerCase(),marker:node.getAttribute('data-marker'),text:text(node)});
      return {id:item.id,chain};
    });
    const productPage=/^\/draft\/\d+$/.test(location.pathname) || /_\d{8,}$/.test(location.pathname);
    const pageText=productPage ? document.body.innerText.slice(0,40000) : undefined;
    const productImages=productPage ? [...document.images].slice(0,100).map(el=>{
      let source;try{const u=new URL(el.currentSrc || el.src);source=u.origin+u.pathname;}catch{source='';}
      const parents=[];let parent=el.parentElement;
      for(let i=0;parent && i<3;i++,parent=parent.parentElement) parents.push({tag:parent.tagName.toLowerCase(),className:parent.className,marker:parent.getAttribute('data-marker')});
      return {source,alt:el.alt,marker:el.getAttribute('data-marker'),visible:visible(el),loaded:el.complete && el.naturalWidth>0,parents};
    }):undefined;
    const uploadedImages=[...document.querySelectorAll('img[data-marker]')].filter(el=>/^preview\(.+\)\/image$/.test(el.getAttribute('data-marker'))).map(el=>({marker:el.getAttribute('data-marker'),complete:el.complete && el.naturalWidth>0,loading:el.loading,top:Math.round(el.getBoundingClientRect().top),sourceOrigin:(()=>{try{return new URL(el.currentSrc || el.src).origin;}catch{return '';}})(),source:(()=>{try{const u=new URL(el.currentSrc || el.src);return u.origin+u.pathname;}catch{return '';}})()}));
    const accountProfile=(location.pathname==='/profile' || location.pathname.startsWith('/profile/items/')) && headings.includes('Мои объявления') && controls.some(c=>c.role==='tab' && c.marker.startsWith('personal-items-tabs/tab(')) && !challenge;
    return {inspectorVersion:2,title:document.title,origin:location.origin,path:location.pathname,headings,tabLabels,controls,markers,listingLinks,ancestors,
      pageText,uploadedImages,productImages,
      truncated:elements.length>2000 || markerElements.length>2500,challenge,pagination:{hasNext:paginationNext().length===1},
      accountVerified:accountProfile,accountAccessible:accountProfile || /^\/draft\/\d+$/.test(location.pathname) && controls.some(c=>c.marker==='ItemDraftManageButtons/edit'),
      inventoryComplete:false,
      reason:challenge?'Требуется ручное подтверждение на сайте. Автоматизация остановлена.':'Снимок реального DOM; полнота аккаунта и назначение элементов требуют проверки адаптера.'};
  }
  async function settle() {
    // Wait for actual DOM changes to quiet down; bounded even on animated pages.
    await new Promise(resolve=>{
      let quiet;
      const finish=()=>{clearTimeout(quiet);clearTimeout(limit);observer.disconnect();resolve();};
      const observer=new MutationObserver(()=>{clearTimeout(quiet);quiet=setTimeout(finish,500);});
      const limit=setTimeout(finish,12000);
      observer.observe(document.body,{subtree:true,childList:true,characterData:true});
      quiet=setTimeout(finish,500);
    });
  }
  const listener=(message,sender,respond)=>{
    if(sender.id!==chrome.runtime.id || message.channel!=='marketplace-transfer-v1') return;
    const p=message.payload || {};
    let afterResponse;
    if(message.action==='inspect') {respond(inspect());return;}
    (async()=>{
      if(inspect().challenge) throw new Error('Требуется ручная проверка безопасности');
      if(message.action==='tab') {
        if(!/^personal-items-tabs\/tab\([a-zA-Z0-9_-]+\)$/.test(p.marker) || !(location.pathname==='/profile' || location.pathname.startsWith('/profile/items/'))) throw new Error('Не открыт доступный раздел объявлений аккаунта');
        const elements=[...document.querySelectorAll('[role="tab"]')].filter(el=>visible(el) && el.getAttribute('data-marker')===p.marker);
        if(elements.length!==1) throw new Error('Не найден однозначный раздел объявлений');
        elements[0].click();await settle();return inspect();
      }
      if(message.action==='nextPage') {
        if(!(location.pathname==='/profile' || location.pathname.startsWith('/profile/items/'))) throw new Error('Пагинация разрешена только в списке объявлений');
        const candidates=paginationNext();
        if(candidates.length!==1) throw new Error('Следующая страница не найдена однозначно');
        afterResponse=()=>candidates[0].click();return {pageRequested:true};
      }
      if(message.action==='scroll') {
        window.scrollTo(0,document.documentElement.scrollHeight);await settle();return inspect();
      }
      if(message.action==='category') {
        if(location.pathname!=='/additem') throw new Error('Выбор категории разрешён только в форме объявления');
        const label=value=>String(value).replace(/\s+/g,' ').trim();
        const candidates=[...document.querySelectorAll('button[data-marker="category-wizard/button"], [data-marker="category-suggests"] button[data-marker="category-title"]')].filter(el=>visible(el) && label(text(el))===label(p.label));
        if(candidates.length!==1) throw new Error('Категория не найдена однозначно в текущем интерфейсе');
        candidates[0].click();await settle();return inspect();
      }
      if(message.action==='fill') {
        const allowedName=['title','description'].includes(p.name);
        const allowedMarker=['price','params[199748]/input'].includes(p.marker);
        if(location.pathname!=='/additem' || !(allowedName || allowedMarker)) throw new Error('Поле ещё не проверено на реальной форме');
        const fields=[...document.querySelectorAll('input,textarea')].filter(el=>visible(el) && (allowedName?el.name===p.name:el.getAttribute('data-marker')===p.marker) && !['password','hidden','email','tel'].includes(el.type));
        if(fields.length!==1 || typeof p.value!=='string' || p.value.length>20000) throw new Error('Поле не найдено однозначно или значение недопустимо');
        const field=fields[0];
        const proto=field.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto,'value').set.call(field,p.value);
        field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));field.blur();
        await settle();return {...inspect(),filled:{name:p.name,verified:field.value===p.value}};
      }
      if(message.action==='photoStart') {
        if(location.pathname!=='/additem' || !/^[a-f0-9]{64}$/.test(p.id) || !['image/jpeg','image/png','image/webp'].includes(p.mime) || !Number.isInteger(p.size) || p.size<=0 || p.size>20*1024*1024) throw new Error('Некорректные параметры оригинала фотографии');
        const total=[...globalThis.__MTphotoBuffers.values()].reduce((sum,x)=>sum+x.size,0);
        if(total+p.size>128*1024*1024) throw new Error('Превышен лимит буфера фотографий');
        globalThis.__MTphotoBuffers.set(p.id,{name:p.filename,mime:p.mime,size:p.size,chunks:[],received:0});
        return {started:true,id:p.id};
      }
      if(message.action==='photoChunk') {
        const buffer=globalThis.__MTphotoBuffers.get(p.id);
        if(!buffer || p.offset!==buffer.received || typeof p.base64!=='string' || p.base64.length>100000) throw new Error('Нарушена последовательность передачи фотографии');
        const raw=Uint8Array.from(atob(p.base64),c=>c.charCodeAt(0));
        if(buffer.received+raw.length>buffer.size) throw new Error('Превышен заявленный размер фотографии');
        buffer.chunks.push(raw);buffer.received+=raw.length;
        return {received:buffer.received};
      }
      if(message.action==='photoAttach') {
        if(location.pathname!=='/additem' || !Array.isArray(p.ids) || p.ids.length<1 || p.ids.length>20) throw new Error('Некорректный набор фотографий');
        const inputs=[...document.querySelectorAll('input[type="file"][data-marker="add/input"]')].filter(el=>el.accept.split(',').some(mime=>mime.trim().startsWith('image/')));
        if(inputs.length!==1) throw new Error('Поле фотографий не найдено однозначно');
        const transfer=new DataTransfer();
        for(const id of p.ids) {
          const buffer=globalThis.__MTphotoBuffers.get(id);
          if(!buffer || buffer.received!==buffer.size) throw new Error('Фотография передана не полностью');
          const file=new File(buffer.chunks,buffer.name,{type:buffer.mime});
          const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('');
          if(digest!==id) throw new Error('Контрольная сумма фотографии не совпала');
          transfer.items.add(file);
        }
        inputs[0].files=transfer.files;inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
        for(const id of p.ids) globalThis.__MTphotoBuffers.delete(id);
        await settle();return {...inspect(),filesProvided:p.ids.length,checksumsVerified:true};
      }
      if(message.action==='inspectOptions') {
        if(location.pathname!=='/additem' || !['params[199748]/input','type_of_trade/search-input'].includes(p.marker)) throw new Error('Поле списка ещё не проверено');
        const inputs=[...document.querySelectorAll('input[data-marker]')].filter(el=>el.getAttribute('data-marker')===p.marker && visible(el));
        if(inputs.length!==1) throw new Error('Поле списка не найдено однозначно');
        inputs[0].click();await settle();return inspect();
      }
      if(message.action==='selectCountry') {
        if(location.pathname!=='/additem' || typeof p.label!=='string' || !p.label.trim()) throw new Error('Страна должна быть задана исходными данными');
        const candidates=[...document.querySelectorAll('[data-marker="params[199748]/option"]')].filter(el=>visible(el) && text(el)===p.label);
        if(candidates.length!==1) throw new Error('Страна не найдена однозначно в списке Авито');
        candidates[0].click();await settle();return inspect();
      }
      if(message.action==='selectCondition') {
        if(location.pathname!=='/additem' || !['Новое','Б/у'].includes(p.label)) throw new Error('Состояние должно быть задано исходными данными');
        const candidates=[...document.querySelectorAll('label[data-marker]')].filter(el=>['params[110369]/option(','params[110406]/option('].some(prefix=>el.getAttribute('data-marker').startsWith(prefix)) && visible(el) && text(el)===p.label);
        if(candidates.length!==1) throw new Error('Состояние не найдено однозначно');
        candidates[0].click();await settle();return inspect();
      }
      if(message.action==='clearCountry') {
        if(location.pathname!=='/additem') throw new Error('Не открыта форма объявления');
        const buttons=[...document.querySelectorAll('button[data-marker="params[199748]/clear"]')].filter(visible);
        if(buttons.length!==1) throw new Error('Очистка страны недоступна');
        buttons[0].click();await settle();return inspect();
      }
      if(message.action==='removeFailedPhotos') {
        if(location.pathname!=='/additem') throw new Error('Не открыта форма объявления');
        const before=inspect().uploadedImages;
        if(!before.some(image=>image.complete)) throw new Error('Нет ни одной загруженной фотографии; сохранение запрещено');
        const removed=[];
        for(const image of before.filter(i=>!i.complete)) {
          const actual=[...document.querySelectorAll('img[data-marker]')].find(el=>el.getAttribute('data-marker')===image.marker);
          if(actual && actual.complete && actual.naturalWidth) continue;
          const marker=image.marker.replace(/\/image$/,'/delete-button');
          const buttons=[...document.querySelectorAll('button[data-marker]')].filter(el=>el.getAttribute('data-marker')===marker && visible(el));
          if(buttons.length!==1) throw new Error('Не удалось однозначно удалить незагруженную фотографию');
          buttons[0].click();removed.push(image.marker);await settle();
        }
        return {...inspect(),removedPhotoMarkers:removed};
      }
      if(message.action==='clearPhotos') {
        if(location.pathname!=='/additem') throw new Error('Не открыта форма объявления');
        for(let index=0;index<20;index++) {
          const buttons=[...document.querySelectorAll('button[data-marker]')].filter(el=>/^preview\(.+\)\/delete-button$/.test(el.getAttribute('data-marker')) && visible(el) && el.closest('[data-marker="images/drop-zone"],[data-marker="images"]'));
          if(!buttons.length) return {...inspect(),cleared:true};
          buttons[0].click();await settle();
        }
        throw new Error('Не удалось подтвердить очистку набора фотографий');
      }
      if(message.action==='retryPreview') {
        if(location.pathname!=='/additem') throw new Error('Не открыта форма объявления');
        const images=[...document.querySelectorAll('img[data-marker]')].filter(el=>/^preview\(.+\)\/image$/.test(el.getAttribute('data-marker')) && (!el.complete || !el.naturalWidth));
        for(const image of images) {
          const u=new URL(image.currentSrc || image.src);
          if(u.protocol!=='https:' || !u.hostname.endsWith('.img.avito.st')) throw new Error('Недопустимый источник миниатюры');
          image.loading='eager';image.scrollIntoView({block:'center'});image.src=u.href;
        }
        await settle();return inspect();
      }
      if(message.action==='saveDraft') {
        if(location.pathname!=='/additem') throw new Error('Не открыта форма объявления');
        const buttons=[...document.querySelectorAll('button[data-marker="item-creator/save-and-exit"]')].filter(el=>visible(el) && !el.disabled && text(el)==='Сохранить и выйти');
        if(buttons.length!==1) throw new Error('Отдельная кнопка сохранения черновика недоступна');
        const state=inspect();
        const errors=state.markers.filter(m=>m.marker==='field/error' && m.text);
        if(errors.length) throw new Error('Ошибки формы: '+errors.map(m=>m.text).join('; '));
        if(!state.uploadedImages.length || state.uploadedImages.some(i=>!i.complete)) throw new Error('Не все фотографии загружены');
        afterResponse=()=>buttons[0].click();return {saveRequested:true};
      }
      if(message.action==='openListing') {
        const target=new URL(p.path,location.origin);
        if(target.origin!==location.origin || !(/^\/draft\/\d+$/.test(target.pathname) || /_\d{8,}$/.test(target.pathname))) throw new Error('Разрешены только сохранённые ссылки на объявления Авито');
        afterResponse=()=>{location.href=target.href;};
        return {navigating:true,path:target.pathname};
      }
      if(message.action==='openSavedDraft') {
        if(!/^\d+$/.test(String(p.id))) throw new Error('Некорректный ID сохранённого черновика');
        afterResponse=()=>{location.href=new URL('/draft/'+p.id,location.origin).href;};
        return {navigating:true,path:'/draft/'+p.id};
      }
      if(message.action==='editDraft') {
        if(!/^\d+$/.test(String(p.id)) || location.pathname!=='/draft/'+p.id) throw new Error('Сначала откройте подтверждённый черновик');
        const links=[...document.querySelectorAll('a[data-marker="ItemDraftManageButtons/edit"]')].filter(visible);
        if(links.length!==1) throw new Error('Ссылка редактирования черновика не найдена однозначно');
        const target=new URL(links[0].href);
        if(target.origin!==location.origin || target.pathname!=='/additem') throw new Error('Ссылка редактирования имеет неожиданное назначение');
        afterResponse=()=>{location.href=target.href;};
        return {editingDraft:true,id:p.id};
      }
      if(message.action==='navigate') {
        const path=p.path;
        const link=[...document.querySelectorAll('a[href]')].find(el=>{
          try{const u=new URL(el.href);return visible(el) && u.origin===location.origin && u.pathname===path;}catch{return false;}
        });
        if(!link || !(path==='/profile' || path==='/additem' || /_\d{8,}$/.test(path) || /^\/draft\/\d+$/.test(path))) throw new Error('Переход разрешён только по обнаруженной ссылке объявления или формы');
        // Navigation itself never submits the form or clicks publication/payment controls.
        afterResponse=()=>{location.href=new URL(path,location.origin).href;};
        return {navigating:true,path};
      }
      throw new Error('Операция не разрешена');
    })().then(result=>{respond(result);if(afterResponse) afterResponse();}).catch(error=>respond({error:error.message}));
    return true;
  };
  globalThis.__marketplaceTransferInspectorV2=listener;
  chrome.runtime.onMessage.addListener(listener);
})();
