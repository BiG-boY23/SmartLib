/* Exercise the actual native handlers/components without loading Expo or user data. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const mobile=path.resolve('mobile-native');
const parser=require(path.join(mobile,'node_modules/@babel/parser'));
const babel=require(path.join(mobile,'node_modules/@babel/core'));
const jsx=require(path.join(mobile,'node_modules/@babel/plugin-transform-react-jsx'));
const source=fs.readFileSync(path.join(mobile,'App.js'),'utf8');
const ast=parser.parse(source,{sourceType:'module',plugins:['jsx']});
const declarations=new Map();
function walk(node){
  if(!node||typeof node!=='object')return;
  if(node.type==='FunctionDeclaration'&&node.id)declarations.set(node.id.name,node);
  if(node.type==='VariableDeclarator'&&node.id.type==='Identifier')declarations.set(node.id.name,node);
  for(const value of Object.values(node))if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);
}
walk(ast);
function extract(name){const node=declarations.get(name);assert.ok(node,`${name} exists in App.js`);return (node.type==='VariableDeclarator'?'const ':'')+source.slice(node.start,node.end)+';';}
const thread={id:7,subject:'Research support',created_at:'2026-10-20T01:00:00',updated_at:'2026-10-20T01:05:00',is_closed:false};
const own={id:15,sender_id:3,sender_name:'Reader',is_staff:false,message:'Please extend my research loan.',created_at:'2026-10-20T01:00:00'};
const incoming={id:16,sender_id:4,sender_name:'Librarian',is_staff:true,message:'We will review your request.',created_at:'2026-10-20T01:05:00'};
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};

function handlers(isStaff=false){
  const requests=[],alerts=[];
  let response;
  const context=vm.createContext({URLSearchParams,isStaff,useCallback:fn=>fn,
    activeThread:clone(thread),threadMessages:clone([own,incoming]),messageThreads:[clone(thread)],messageDraft:'  Research update  ',
    messageBusy:false,messageBusyRef:{current:false},messageLoadVersion:{current:0},messageListVersion:{current:0},activeMessageId:{current:thread.id},
    Alert:{alert:(title,description,buttons)=>alerts.push({title,description,buttons})},
    request:async(path,options={})=>{requests.push({path,options});if(response)return response(path,options);return path.endsWith('/messages')?[]:{thread:clone(thread),messages:clone([own,incoming])};},
  });
  context.setActiveThread=value=>{context.activeThread=value;};
  context.setThreadMessages=value=>{context.threadMessages=typeof value==='function'?value(context.threadMessages):value;};
  context.setMessageThreads=value=>{context.messageThreads=value;};
  context.setMessageDraft=value=>{context.messageDraft=value;};
  context.setMessageBusy=value=>{context.messageBusy=value;};
  vm.runInContext(['closeMessageThread','loadMessageThreads','loadMessageThread','sendThreadReply','confirmMessageDeletion'].map(extract).join('\n')+
    '\nglobalThis.actions={closeMessageThread,loadMessageThreads,loadMessageThread,sendThreadReply,confirmMessageDeletion};',context);
  return {context,requests,alerts,actions:context.actions,respond:fn=>{response=fn;},accept:()=>alerts.findLast(alert=>alert.buttons)?.buttons.find(button=>button.style==='destructive').onPress()};
}

function screen(overrides={}){
  const calls=[];
  const context=vm.createContext({h:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),
    Fragment:'Fragment',Greeting:'Greeting',Button:'Button',View:'View',Text:'Text',TextInput:'TextInput',Pressable:'Pressable',SmartLibIcon:'SmartLibIcon',EmptyState:'EmptyState',
    styles:{},COLORS:{wine:'#730d24',muted:'#777'},parseServerTimestamp:value=>new Date(value),
    props:{user:{id:3},isStaff:false,threads:[],activeThread:thread,messages:[own,incoming],draft:'Reply',onOpen(){},onBack(){},onSend(){},onCompose(){},setDraft(){},onDelete:(...args)=>calls.push(args),...overrides},
  });
  const compiled=babel.transformSync(extract('MessagesScreen'),{configFile:false,babelrc:false,plugins:[[jsx,{pragma:'h',pragmaFrag:'Fragment'}]]}).code;
  vm.runInContext(compiled+'\nglobalThis.tree=MessagesScreen(props);',context);
  const nodes=[];
  const visit=node=>{if(!node||typeof node!=='object')return;if(Array.isArray(node)){node.forEach(visit);return;}nodes.push(node);node.children?.forEach(visit);};visit(context.tree);
  return {nodes,calls};
}

test('native conversation exposes clear/delete and own-message deletion; staff can delete either sender',()=>{
  const patron=screen();
  for(const title of ['Clear messages','Delete conversation'])assert.ok(patron.nodes.some(node=>node.type==='Button'&&node.props.title===title));
  const deletes=patron.nodes.filter(node=>node.props.accessibilityLabel?.startsWith('Delete message from'));
  assert.equal(deletes.length,1);assert.equal(deletes[0].props.accessibilityLabel,'Delete message from you');
  deletes[0].props.onPress();assert.equal(patron.calls[0][0],'message');assert.equal(patron.calls[0][1].id,own.id);
  for(const title of ['Clear messages','Delete conversation'])patron.nodes.find(node=>node.type==='Button'&&node.props.title===title).props.onPress();
  assert.deepEqual(patron.calls.slice(1).map(args=>args[0]),['clear','conversation']);
  assert.equal(screen({isStaff:true}).nodes.filter(node=>node.props.accessibilityLabel?.startsWith('Delete message from')).length,2);
});

test('busy disables every conversation action and empty/closed conversations keep management visible',()=>{
  const busy=screen({busy:true});
  for(const node of busy.nodes.filter(node=>['Button','Pressable'].includes(node.type)))assert.equal(node.props.disabled,true);
  assert.equal(busy.nodes.find(node=>node.type==='TextInput').props.editable,false);
  const empty=screen({messages:[],activeThread:{...thread,is_closed:true}});
  assert.equal(empty.nodes.find(node=>node.type==='Button'&&node.props.title==='Clear messages').props.disabled,true);
  assert.ok(empty.nodes.some(node=>node.type==='Button'&&node.props.title==='Delete conversation'));
  assert.ok(!empty.nodes.some(node=>node.type==='TextInput'));
});

test('cancel sends no deletion and confirmation explains retained SuperAdmin archive',()=>{
  const h=handlers();h.actions.confirmMessageDeletion('message',own);
  const alert=h.alerts[0];assert.ok(alert.description.includes('only the SuperAdmin'));
  const cancel=alert.buttons.find(button=>button.style==='cancel');cancel.onPress?.();assert.equal(h.requests.length,0);
});

for(const isStaff of [false,true])for(const kind of ['message','clear','conversation']){
  test(`${isStaff?'staff':'patron'} ${kind} DELETE uses immutable snapshot and correct API scope`,async()=>{
    const h=handlers(isStaff);h.actions.confirmMessageDeletion(kind,own);await h.accept();
    const deletes=h.requests.filter(request=>request.options.method==='DELETE');assert.equal(deletes.length,1);
    const url=new URL(deletes[0].path,'http://localhost');
    const suffix=kind==='message'?`/messages/${own.id}`:kind==='clear'?'/messages':'';
    assert.equal(url.pathname,`${isStaff?'/admin':'/mobile'}/messages/${thread.id}${suffix}`);
    assert.equal(url.searchParams.get('expected_created_at'),kind==='message'?own.created_at:thread.created_at);
    assert.equal(url.searchParams.get(kind==='message'?'expected_thread_created_at':'expected_updated_at'),kind==='message'?thread.created_at:thread.updated_at);
    if(kind==='conversation'){assert.equal(h.context.activeThread,null);assert.equal(h.context.threadMessages.length,0);}
  });
}

test('switching conversations after confirmation cannot delete either conversation',async()=>{
  const h=handlers();h.actions.confirmMessageDeletion('conversation');
  h.context.activeThread={...thread,id:8};h.context.activeMessageId.current=8;
  await h.accept();assert.equal(h.requests.length,0);assert.equal(h.context.activeThread.id,8);
});

test('concurrent confirmation acceptance sends one DELETE and blocks duplicate replies',async()=>{
  const h=handlers(),pending=deferred();
  h.respond((path,options)=>options.method==='DELETE'?pending.promise:[]);
  h.actions.confirmMessageDeletion('conversation');const deletion=h.accept();
  await h.accept();await h.actions.sendThreadReply();h.actions.confirmMessageDeletion('clear');
  assert.equal(h.requests.filter(request=>request.options.method==='DELETE').length,1);
  assert.equal(h.requests.filter(request=>request.options.method==='POST').length,0);
  assert.equal(h.alerts.length,1);assert.equal(h.context.messageBusy,true);
  pending.resolve({});await deletion;assert.equal(h.context.messageBusy,false);
});

test('older thread and inbox polling cannot restore a deleted conversation',async()=>{
  const h=handlers(),threadPoll=deferred(),listPoll=deferred();let threadCalls=0,listCalls=0;
  h.respond((path,options)=>{
    if(options.method==='DELETE')return {};
    if(path==='/mobile/messages/7'&&++threadCalls===1)return threadPoll.promise;
    if(path==='/mobile/messages'&&++listCalls===1)return listPoll.promise;
    return [];
  });
  const detail=h.actions.loadMessageThread(thread.id),list=h.actions.loadMessageThreads();
  h.actions.confirmMessageDeletion('conversation');await h.accept();
  threadPoll.resolve({thread:clone(thread),messages:clone([own,incoming])});listPoll.resolve([clone(thread)]);
  await Promise.all([detail,list]);
  assert.equal(h.context.activeThread,null);assert.equal(h.context.activeMessageId.current,null);
  assert.equal(h.context.threadMessages.length,0);assert.equal(h.context.messageThreads.length,0);
});

test('missing snapshot prevents deletion and requires refresh',async()=>{
  const h=handlers();h.context.activeThread={...thread,updated_at:null};h.actions.confirmMessageDeletion('clear');await h.accept();
  assert.equal(h.requests.length,0);assert.equal(h.alerts.at(-1).title,'Refresh required');
});

test('a pending reply serializes message actions and sends the captured body only once',async()=>{
  const h=handlers(),pending=deferred();
  h.respond((path,options)=>options.method==='POST'?pending.promise:{thread:clone(thread),messages:[clone(own)]});
  const reply=h.actions.sendThreadReply();h.context.messageDraft='Changed draft';await h.actions.sendThreadReply();h.actions.confirmMessageDeletion('conversation');
  const sent=h.requests.filter(request=>request.options.method==='POST');assert.equal(sent.length,1);
  assert.equal(JSON.parse(sent[0].options.body).message,'Research update');assert.equal(h.alerts.length,0);
  pending.resolve({});await reply;assert.equal(h.context.messageBusy,false);
});
