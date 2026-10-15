'use strict';
const $ = id => document.getElementById(id);
const modes = {
  t2v: {title:'描述你想看到的画面', help:'描述主体、动作、环境、镜头与画面风格。', max:0, example:'一只橘猫在阳光下的草地上追逐蝴蝶，镜头缓慢跟随，微风吹动草叶，写实风格，暖色调，动作自然流畅。', doc:'text-to-video'},
  i2v: {title:'让一张图片动起来', help:'上传首帧图片，再描述主体动作与镜头运动。提示词可留空。', min:1, max:1, imageHelp:'仅 1 张，JPEG / PNG / WEBP，≤20 MB；宽高均 ≥300 px，宽高比 1:2.5～2.5:1。输出比例跟随图片。', example:'镜头缓慢推近，主体轻轻转头，微风吹动衣服，背景保持稳定，动作自然流畅。', doc:'image-to-video'},
  r2v: {title:'让参考素材进入同一段故事', help:'用 [Image 1]、[Image 2] 指代下方对应图片中的具体人物或物体。', min:1, max:9, imageHelp:'1～9 张，JPEG / PNG / WEBP，≤20 MB；短边 ≥400 px。按下方编号引用图片。', example:'[Image 1] 中的人物走进阳光洒落的花园，镜头缓慢跟随，微风轻轻吹动衣服，保持人物外观一致，电影质感。', doc:'reference-to-video'},
  edit: {title:'给现有视频一个新的方向', help:'输入公网视频链接，描述风格变换或局部替换意图；可添加参考图片。', max:5, imageHelp:'参考图可选，最多 5 张，≤20 MB；宽高均 ≥300 px，宽高比 1:2.5～2.5:1。', example:'将视频转换为温暖的手绘水彩风格，保持人物动作、镜头运动与场景构图一致。', doc:'video-edit'}
};
const modeNames = {t2v:'文生视频',i2v:'首帧图生视频',r2v:'参考生视频',edit:'视频编辑'};
let mode = 't2v', assets = {t2v:[],i2v:[],r2v:[],edit:[]}, timer, epoch = 0, active = null;
let creating = false, loadingImages = false, querying = false, failures = 0;
let history = [];
function readStorage(key, fallback) { try {return JSON.parse(localStorage.getItem(key)) ?? fallback;} catch {return fallback;} }
function store(key, value) {try {localStorage.setItem(key, JSON.stringify(value));} catch { /* Local storage may be unavailable. */ }}
function message(text, error=false) {$('message').textContent=text; $('message').classList.toggle('error',error);}
function settings() {return {api_key:$('api-key').value.trim(),workspace:$('workspace').value.trim(),region:$('region').value,billing:$('billing').value,mode,version:$('version').value};}
function tokenPlan(config=settings()) {return config.billing==='tokenplan' || (config.billing==='auto' && config.api_key.startsWith('sk-sp-'));}
function updateConnection() {
  const plan=tokenPlan();
  $('workspace-field').hidden=plan;
  $('region').disabled=plan;
  if(plan) $('region').value='ap-southeast-1';
  $('billing-help').textContent=plan?'使用 TokenPlan 专用接口，无需业务空间 ID。sk-sp- 前缀也可能属于 Coding Plan，请确认 Key 来自 TokenPlan“我的订阅”。':'普通模型 Key 使用业务空间接口；自动模式检测到 sk-sp- 时切换到 TokenPlan。';
  $('region-help').textContent=plan?'TokenPlan 专用接口固定使用新加坡地域。':'Key、模型与接口必须属于同一地域。';
  $('billing-note').textContent=plan?'消耗 TokenPlan Credits，不自动切换按量付费':'生成请求按阿里云账户规则计费';
  if(plan && mode==='edit') $('mode-help').textContent=modes.edit.help+' 视频编辑模型是否可用，以 TokenPlan 套餐支持列表为准。';
  else $('mode-help').textContent=modes[mode].help;
}
function saveSettings() {const {api_key,mode,...rest}=settings(); store('hh-settings',rest);}
function buttons() {
  $('generate').disabled=creating || loadingImages || !!timer || querying;
  $('query').disabled=creating || querying;
  $('generate').textContent=creating?'正在提交…':timer || querying?'正在等待视频…':'生成视频 ↗';
  document.querySelectorAll('[data-mode]').forEach(button=>button.disabled=creating || loadingImages);
}
function setMode(next) {
  mode=next;
  const info=modes[mode];
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.mode===mode)));
  $('mode-title').textContent=info.title; $('mode-help').textContent=info.help;
  $('image-field').hidden=mode==='t2v'; $('video-field').hidden=mode!=='edit';
  $('ratio-field').hidden=!['t2v','r2v'].includes(mode); $('duration-field').hidden=mode==='edit';
  $('audio-field').hidden=mode!=='edit'; $('version-field').hidden=mode==='edit';
  $('resolution').querySelector('[value="480P"]').disabled=mode==='edit';
  if(mode==='edit' && $('resolution').value==='480P') $('resolution').value='720P';
  $('file-input').multiple=mode!=='i2v';
  $('image-title').textContent=mode==='i2v'?'首帧图片':'参考图片';
  $('image-help').textContent=info.imageHelp || '';
  $('docs-link').href=`https://help.aliyun.com/zh/model-studio/happyhorse-${info.doc}-api-reference`;
  renderAssets();updateConnection();
}
function renderAssets() {
  $('assets').replaceChildren();
  assets[mode].forEach((asset,index)=>{
    const item=document.createElement('div'); item.className='asset';
    const image=document.createElement('img'); image.src=asset.url; image.alt=asset.name;
    const badge=document.createElement('span'); badge.className='badge'; badge.textContent=mode==='i2v'?'首帧':`Image ${index+1}`;
    const remove=document.createElement('button'); remove.className='remove'; remove.textContent='×'; remove.title='移除此图片';
    remove.onclick=()=>{assets[mode].splice(index,1);renderAssets();};
    const caption=document.createElement('small'); caption.textContent=asset.name; caption.title=asset.name;
    item.append(image,badge,remove,caption); $('assets').append(item);
  });
}
function capacity(count=1) {if(assets[mode].length+count>modes[mode].max) throw Error(`此模式最多 ${modes[mode].max} 张图片，请先移除已有图片。`);}
function validUrl(value) {let url;try {url=new URL(value);}catch {throw Error('请输入完整的 HTTP/HTTPS 公网链接。');} if(!['https:','http:'].includes(url.protocol)||url.username||url.password) throw Error('请输入 HTTP/HTTPS 公网链接。');return value;}
function fileData(file) {return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(Error('图片读取失败。'));r.readAsDataURL(file);});}
function dimensions(url) {return new Promise((resolve,reject)=>{const image=new Image(); image.onload=()=>resolve([image.naturalWidth,image.naturalHeight]);image.onerror=()=>reject(Error('无法读取图片。'));image.src=url;});}
$('file-input').addEventListener('change',async event=>{
  const files=[...event.target.files]; if(!files.length) return;
  const selectedMode=mode; loadingImages=true;buttons();
  try {
    capacity(files.length); const added=[];
    for(const file of files) {
      if(!['image/jpeg','image/png','image/webp'].includes(file.type)) throw Error(`${file.name}：仅支持 JPEG、PNG、WEBP。`);
      if(file.size>20*1024*1024) throw Error(`${file.name}：超过 20 MB。`);
      const url=await fileData(file), [w,h]=await dimensions(url);
      if(selectedMode==='r2v' ? Math.min(w,h)<400 : Math.min(w,h)<300 || w/h<.4 || w/h>2.5) throw Error(`${file.name}：图片尺寸或宽高比不符合要求。`);
      added.push({name:file.name,url});
    }
    // Recheck after asynchronous reads in case a URL was added meanwhile.
    if(assets[selectedMode].length+added.length>modes[selectedMode].max) throw Error('图片数量超过此模式上限。');
    assets[selectedMode].push(...added); renderAssets();message('图片已就绪，提交时发送到阿里云。');
  } catch(error) {message(error.message,true);} finally {event.target.value='';loadingImages=false;buttons();}
});
$('add-url').onclick=()=>{try{capacity();const url=validUrl($('image-url').value.trim());assets[mode].push({url,name:'公网图片'});$('image-url').value='';renderAssets();message('图片链接已添加；远端图片尺寸与可访问性由云端校验。');}catch(error){message(error.message,true);}};
async function api(path, data) {
  let response;
  try {response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':document.querySelector('meta[name="app-token"]').content},body:JSON.stringify(data)});}catch {throw Error(path==='/api/create'?'提交响应未收到，任务可能已经创建。请先在百炼控制台检查，避免重复计费。':'本地服务连接失败，请确认启动窗口仍在运行。');}
  let result;try {result=await response.json();}catch {throw Error('接口返回了无法解析的响应。');}
  if(!response.ok) throw Error(result.error || `请求失败：${response.status}`);
  return result;
}
function upsert(task, status) {
  const old=history.find(item=>item.id===task.id);
  const entry={id:task.id,mode:task.config.mode,region:task.config.region,workspace:task.config.workspace,billing:tokenPlan(task.config)?'tokenplan':'payg',status,time:old?.time || new Date().toISOString()};
  history=[entry,...history.filter(item=>item.id!==entry.id)].slice(0,20);store('hh-history',history);renderHistory();
}
function renderHistory() {
  $('history-list').replaceChildren();
  if(!history.length){const p=document.createElement('p');p.className='hint';p.textContent='还没有任务。';$('history-list').append(p);return;}
  history.forEach(entry=>{
    const row=document.createElement('div');row.className='history-item';const label=document.createElement('span'),title=document.createElement('strong');
    title.textContent=`${modeNames[entry.mode] || entry.mode} · ${entry.status}`;
    label.append(title,document.createTextNode(`${new Date(entry.time).toLocaleString()} · ${entry.id}`));
    const button=document.createElement('button');button.className='secondary';button.textContent='恢复';
    button.onclick=()=>{if(creating||querying){message('请等待当前请求完成。',true);return;}pause();$('billing').value=entry.billing || 'payg';setMode(entry.mode);$('region').value=entry.region;$('workspace').value=entry.workspace;$('task-id').value=entry.id;updateConnection();message('任务已恢复，请填写对应接入方式的 Key 后点击“继续查询”。');};
    row.append(label,button);$('history-list').append(row);
  });
}
function pause(show=false) {
  epoch++;clearTimeout(timer);timer=null;active=null;$('stop').hidden=true;buttons();
  if(show) message('已暂停查询，云端任务继续执行。可通过任务 ID 恢复。');
}
function resetResult() {$('preview').pause();$('preview').removeAttribute('src');$('preview').load();$('preview').hidden=true;$('empty').hidden=false;$('result-actions').hidden=true;$('saved-path').textContent='';$('retry-download').hidden=true;}
async function downloadVideo(task, currentEpoch) {
  $('retry-download').hidden=true; $('saved-path').textContent='正在保存到本地…';
  try {
    const result=await api('/api/download',{task_id:task.id});
    if(currentEpoch!==epoch) return;
    $('preview').src=result.local_url;$('download').href=result.local_url;$('download').download=`happyhorse-${task.id}.mp4`;
    $('saved-path').textContent=`已保存：${result.path}`; message('视频已完成，并保存到本地。');
  }catch(error){if(currentEpoch!==epoch)return;$('saved-path').textContent=`自动保存失败：${error.message}。可从云端视频链接保存。`;$('retry-download').hidden=false;}
}
async function poll(task, currentEpoch) {
  if(currentEpoch!==epoch) return;
  timer=null;querying=true;buttons();let terminal=false;
  try {
    const response=await api('/api/query',{...task.config,task_id:task.id});
    if(currentEpoch!==epoch) return;
    failures=0;$('response').textContent=JSON.stringify(response,null,2);
    const out=response.output || {}, status=out.task_status;
    upsert(task,status || 'UNKNOWN');
    $('status').textContent=({PENDING:'排队中',RUNNING:'处理中',SUCCEEDED:'已完成',FAILED:'失败',CANCELED:'已取消',UNKNOWN:'任务不存在或已过期'})[status] || status || '未知状态';
    if(status==='SUCCEEDED') {
      terminal=true;
      if(!out.video_url) throw Error('任务成功，但未返回视频链接。请查看接口响应。');
      const url=validUrl(out.video_url);$('preview').src=url;$('preview').hidden=false;$('empty').hidden=true;
      $('result-actions').hidden=false;$('original').href=url;$('download').href=url;$('download').download=`happyhorse-${task.id}.mp4`;
      message('视频已生成，正在自动保存。');
      $('retry-download').onclick=()=>downloadVideo(task,currentEpoch);
      await downloadVideo(task,currentEpoch);
    } else if(['FAILED','CANCELED','UNKNOWN'].includes(status)) {
      terminal=true;message(out.message || ({FAILED:'生成失败，请查看接口响应中的错误码。',CANCELED:'任务已取消。',UNKNOWN:'任务不存在或已超过 24 小时，请检查任务 ID 与地域。'})[status],true);
    } else if(['PENDING','RUNNING'].includes(status)) {
      message(`${status==='PENDING'?'任务正在排队':'云端正在处理'}，15 秒后继续查询。任务 ID：${task.id}`);
    } else {terminal=true;throw Error('未知任务状态，请查看接口响应。');}
  }catch(error){
    if(currentEpoch!==epoch)return;
    failures++;terminal=terminal || failures>=3;
    message(error.message+(terminal?' 已暂停查询，可用原任务 ID 继续。':' 15 秒后使用原任务 ID 重试查询。'),true);
  }finally{
    querying=false;
    if(currentEpoch===epoch){
      if(terminal){active=null;$('stop').hidden=true;}else{timer=setTimeout(()=>poll(task,currentEpoch),15000);}
    }
    buttons();
  }
}
$('generate').onclick=async()=>{
  if(creating || querying || loadingImages || timer) return;
  const config=settings();
  if(!config.api_key || (!tokenPlan(config) && !config.workspace)){message(tokenPlan(config)?'请填写 TokenPlan API Key。':'请先填写 API Key 和业务空间 ID。',true);return;}
  const prompt=$('prompt').value.trim();
  if(mode!=='i2v' && !prompt){message('请输入提示词。',true);return;}
  if(assets[mode].length<(modes[mode].min||0)){message('请先添加此模式所需的图片。',true);return;}
  const payload={...config,prompt,images:assets[mode].map(a=>a.url),resolution:$('resolution').value,ratio:$('ratio').value,duration:Number($('duration').value),watermark:$('watermark').checked,audio_setting:$('audio').value,seed:$('seed').value===''?null:Number($('seed').value),video_url:$('video-url').value.trim()};
  if(mode==='edit'){try{validUrl(payload.video_url);}catch(error){message(error.message,true);return;}}
  pause();const currentEpoch=epoch;creating=true;buttons();saveSettings();message('正在提交任务，请等待响应…');
  let task;
  try {
    const response=await api('/api/create',payload);$('response').textContent=JSON.stringify(response,null,2);
    const id=response.output?.task_id;
    if(!id) throw Error('响应中没有任务 ID。请在控制台检查后再提交，避免重复计费。');
    resetResult();$('task-id').value=id;task={id,config};active=task;failures=0;upsert(task,response.output.task_status || 'PENDING');$('stop').hidden=false;
  }catch(error){message(error.message,true);}
  finally {creating=false;buttons();}
  if(task) await poll(task,currentEpoch);
};
$('query').onclick=()=>{
  if(creating||querying)return;
  const config=settings(),id=$('task-id').value.trim();
  if(!config.api_key || (!tokenPlan(config) && !config.workspace) || !id){message(tokenPlan(config)?'请填写 TokenPlan Key 和任务 ID。':'请填写 Key、业务空间 ID 和任务 ID。',true);return;}
  pause();resetResult();failures=0;active={id,config};$('stop').hidden=false;poll(active,epoch);
};
$('stop').onclick=()=>pause(true);
$('quit-app').onclick=async()=>{
  if(creating){message('请等提交完成后再退出，确保保存任务 ID。',true);return;}
  pause();
  try{await api('/api/shutdown',{});message('应用已关闭。再次双击 EXE 即可启动，云端任务仍可通过历史记录恢复。');$('generate').disabled=true;$('query').disabled=true;$('quit-app').disabled=true;}
  catch(error){message(error.message,true);}
};
$('example').onclick=()=>{$('prompt').value=modes[mode].example;$('prompt').dispatchEvent(new Event('input'));};
$('duration').oninput=()=>{$('duration-value').textContent=$('duration').value+' 秒';};
$('prompt').oninput=()=>{$('prompt-count').textContent=[...$('prompt').value].length+' 字符';};
$('clear-history').onclick=()=>{history=[];store('hh-history',history);renderHistory();};
$('preview').addEventListener('error',()=>{if(!$('preview').hidden)message('视频预览无法加载，可尝试下载后用本地播放器打开。',true);});
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>setMode(button.dataset.mode));
const saved=readStorage('hh-settings',{});
if(saved && typeof saved==='object') for(const name of ['workspace','region','version','billing']) {if(saved[name]) $(name).value=saved[name];}
const storedHistory=readStorage('hh-history',[]);
if(Array.isArray(storedHistory)) history=storedHistory.filter(x=>x && typeof x.id==='string' && modes[x.mode]).slice(0,20);
for(const name of ['workspace','region','version','billing']) $(name).addEventListener('change',()=>{updateConnection();saveSettings();});
$('api-key').addEventListener('input',updateConnection);
setMode('t2v');renderHistory();
