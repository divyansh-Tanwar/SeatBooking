'use strict';
const $ = id => document.getElementById(id);
const state = {access:null,user:null,event:null,seats:[],selected:null,reservation:null,socket:null,
  refreshing:null,loadingSeats:false,dirty:false,offset:0,eventNext:null,eventPrevious:null,bookingNext:null};
let csrfToken = '', registerMode = false, noticeTimer, reconnectTimer, checkoutKey;
const money = (value,currency='INR') => new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:0}).format(value/100);
const node = (tag,text,className) => { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(className) el.className=className; return el; };
function notice(message){$('notice').textContent=message;$('notice').hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').hidden=true,6000);}
function message(data){const v=data?.error?.message;return typeof v==='string'?v:JSON.stringify(v||data);}
async function refreshAccess(){
  if(state.refreshing) return state.refreshing;
  const perform=async()=>{
    const r=await fetch('/api/auth/refresh',{method:'POST',headers:{'X-CSRFToken':csrfToken},credentials:'same-origin'});
    if(!r.ok){state.access=null;state.user=null;updateAccount();return false;}
    const data=await r.json();state.access=data.access;state.user=data.user;updateAccount();return true;
  };
  state.refreshing=(navigator.locks?navigator.locks.request('seatlock-refresh',perform):perform()).finally(()=>state.refreshing=null);
  return state.refreshing;
}
async function api(url,options={},retry=true){
  const headers={'Content-Type':'application/json','X-CSRFToken':csrfToken,...options.headers};
  if(state.access) headers.Authorization='Bearer '+state.access;
  const response=await fetch(url,{...options,headers,credentials:'same-origin'});
  if(response.status===401 && retry && !url.startsWith('/api/auth/')){
    if(await refreshAccess()) return api(url,options,false);
  }
  const data=response.status===204?{}:await response.json();
  if(!response.ok) {const err=new Error(message(data));err.status=response.status;throw err;}
  return data;
}
function updateAccount(){$('auth-button').textContent=state.user?state.user.username+' · Sign out':'Sign in ↗';}
function needLogin(){if(state.user)return false;$('auth-dialog').showModal();return true;}
async function loadEvents(url='/api/events'){
  try{
    const data=await api(url);state.eventNext=data.next;state.eventPrevious=data.previous;
    $('next-events').hidden=!data.next;$('previous-events').hidden=!data.previous;$('event-list').replaceChildren();
    data.results.forEach((event,i)=>{
      const card=node('article',undefined,'event-card'), poster=node('div',undefined,'event-poster poster-'+(i%3));
      poster.append(node('span',['LIVE EXPERIENCE','IDEAS & CULTURE','SOUND & SOUL'][i%3],'category'),node('span',event.title.toUpperCase(),'poster-word'));
      const date=new Date(event.starts_at), badge=node('div',undefined,'date-badge');
      badge.append(node('span',date.toLocaleDateString(undefined,{month:'short'}).toUpperCase()),node('b',date.getDate()));poster.append(badge);
      const info=node('div',undefined,'event-info');
      info.append(node('h3',event.title),node('div',event.venue,'muted'),node('div',date.toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'long'})+' · '+date.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}),'muted'));
      const bottom=node('div',undefined,'event-bottom'),button=node('button','Find a seat ↗','button outline');
      button.onclick=()=>openEvent(event);bottom.append(node('span','Reserved seating','price'),button);info.append(bottom);card.append(poster,info);$('event-list').append(card);
    });
    if(!data.results.length)$('event-list').append(node('p','No events yet. Run the demo seed command to add the lineup.','muted'));
  }catch(e){$('event-list').replaceChildren(node('p','Could not load events. '+e.message,'muted'));}
}
function closeSocket(){clearTimeout(reconnectTimer);if(state.socket){state.socket.onclose=null;state.socket.close();state.socket=null;}}
function connectSocket(){
  closeSocket();if(!state.event)return;
  if(document.body.dataset.polling==='true'){$('connection-state').textContent='● Availability refreshed every 5s';return;}
  const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/events/'+state.event.id+'/');state.socket=ws;
  ws.onmessage=()=>{ $('connection-state').textContent='● Live availability';refreshSeats(); };
  ws.onclose=()=>{$('connection-state').textContent='● Reconnecting · polling active';reconnectTimer=setTimeout(connectSocket,5000);};
  ws.onerror=()=>{$('connection-state').textContent='● Polling availability';};
}
async function openEvent(event){
  if(state.reservation?.status==='HELD' && state.reservation.event_id!==event.id){
    notice('Finish or release your current hold before choosing another event.');return;
  }
  if(state.reservation?.status==='CONFIRMED'){state.reservation=null;saveReservation();}
  state.event=event;state.selected=null;
  $('seat-event-title').textContent=event.title;$('seat-panel').hidden=false;$('bookings-panel').hidden=true;
  connectSocket();await refreshSeats();renderCheckout();$('seat-panel').scrollIntoView({behavior:'smooth',block:'start'});
}
async function refreshSeats(){
  if(!state.event)return;
  if(state.loadingSeats){state.dirty=true;return;}
  state.loadingSeats=true;const eventId=state.event.id;
  try{
    const data=await api('/api/events/'+eventId+'/seats');
    if(state.event?.id===eventId){
      state.seats=data.seats;state.offset=Date.parse(data.server_time)-Date.now();renderSeats();
    }
  }catch(e){notice(e.message);}finally{state.loadingSeats=false;if(state.dirty){state.dirty=false;refreshSeats();}}
}
function renderSeats(){
  $('seat-grid').replaceChildren();const columns=Math.max(...state.seats.map(s=>Number(s.label.slice(1))),1);
  $('seat-grid').style.gridTemplateColumns='repeat('+columns+', max-content)';
  $('seat-count').textContent=state.seats.filter(s=>s.status==='AVAILABLE').length+' available';
  for(const seat of state.seats){
    const selected=state.selected?.id===seat.id, button=node('button',seat.label,'seat '+(selected?'selected':seat.status==='HELD'?'held':seat.status==='CONFIRMED'?'booked':''));
    button.disabled=seat.status!=='AVAILABLE'||!!state.reservation;
    button.setAttribute('aria-label','Seat '+seat.label+', '+seat.status.toLowerCase()+', '+money(seat.price,state.event.currency));
    button.setAttribute('aria-pressed',String(selected));button.onclick=()=>{state.selected=seat;renderSeats();renderCheckout();};$('seat-grid').append(button);
  }
}
function renderCheckout(){
  const r=state.reservation,s=state.selected;
  $('reserve-button').hidden=!!r;$('pay-button').hidden=!r||r.status!=='HELD';$('cancel-button').hidden=!r||r.status!=='HELD';
  $('countdown').hidden=!r||r.status!=='HELD';$('price-row').hidden=!r&&!s;
  $('selection-title').textContent=r?(r.status==='CONFIRMED'?"You're going!":'Seat '+r.seat_label):s?'Seat '+s.label:'Pick a seat.';
  $('selection-copy').textContent=r?(r.status==='CONFIRMED'?'Booking '+r.booking_id:'Your seat is held. Complete checkout before the timer ends.'):s?'Ready when you are. Reserve this seat for five minutes.':"Choose any available seat on the map. We'll hold it for five minutes.";
  $('price').textContent=money(r?.price??s?.price??0,r?.currency??state.event?.currency??'INR');
  $('reserve-button').disabled=!s;$('reserve-button').textContent=s?'Reserve seat '+s.label+' →':'Choose a seat';
  $('pay-button').disabled=r?.payment_status==='PENDING'||r?.payment_status==='FAILED';
  $('pay-button').textContent=r?.payment_status==='PENDING'?'Processing payment…':r?.payment_status==='FAILED'?'Payment failed · release seat':'Complete simulated checkout →';
}
function saveReservation(){if(state.reservation)sessionStorage.setItem('seatlock-reservation',state.reservation.id);else sessionStorage.removeItem('seatlock-reservation');}
async function updateReservation(){
  if(!state.reservation)return;
  const id=state.reservation.id;
  try{
    const r=await api('/api/reservations/'+id);
    if(state.reservation?.id!==id)return;
    state.offset=Date.parse(r.server_time)-Date.now();state.reservation=r;
    if(['EXPIRED','CANCELLED'].includes(r.status)){
      notice(r.status==='EXPIRED'?'Your hold expired. You can choose another available seat.':'Your seat has been released.');state.reservation=null;state.selected=null;saveReservation();refreshSeats();
    } else if(r.status==='CONFIRMED'){saveReservation();refreshSeats();}
    renderCheckout();
  }catch(e){if(e.status===404){state.reservation=null;saveReservation();renderCheckout();}else notice(e.message);}
}
$('reserve-button').onclick=async()=>{
  if(needLogin()||!state.selected)return;
  $('reserve-button').disabled=true;
  const seat=state.selected;
  if(!seat.requestKey)seat.requestKey=crypto.randomUUID();
  try{state.reservation=await api('/api/reservations',{method:'POST',headers:{'Idempotency-Key':seat.requestKey},body:JSON.stringify({seat_id:seat.id})});checkoutKey=null;saveReservation();await refreshSeats();renderCheckout();}
  catch(e){notice(e.message);if(e.status&&e.status!==503)seat.requestKey=null;await refreshSeats();renderCheckout();}
};
$('pay-button').onclick=async()=>{
  $('pay-button').disabled=true;checkoutKey=checkoutKey||crypto.randomUUID();
  try{await api('/api/reservations/'+state.reservation.id+'/confirm',{method:'POST',headers:{'Idempotency-Key':checkoutKey},body:'{}'});await updateReservation();notice('Simulated checkout started. Your booking will appear here.');}
  catch(e){notice(e.message);renderCheckout();}
};
$('cancel-button').onclick=async()=>{if(!state.reservation)return;try{await api('/api/reservations/'+state.reservation.id+'/cancel',{method:'POST',body:'{}'});await updateReservation();}catch(e){notice(e.message);}};
$('auth-button').onclick=async()=>{
  if(!state.user){$('auth-dialog').showModal();return;}
  try{await api('/api/auth/logout',{method:'POST',body:'{}'});state.access=null;state.user=null;state.reservation=null;saveReservation();updateAccount();renderCheckout();notice('Signed out.');}catch(e){notice(e.message);}
};
$('close-auth').onclick=()=>$('auth-dialog').close();
$('switch-auth').onclick=()=>{registerMode=!registerMode;$('email-label').hidden=!registerMode;$('auth-submit').textContent=registerMode?'Create account →':'Sign in →';$('switch-auth').textContent=registerMode?'Already a member? Sign in':'New here? Create an account';$('auth-error').textContent='';};
$('auth-form').onsubmit=async e=>{
  e.preventDefault();$('auth-submit').disabled=true;$('auth-error').textContent='';
  const data=Object.fromEntries(new FormData(e.target));if(!registerMode)delete data.email;
  try{const result=await api('/api/auth/'+(registerMode?'register':'login'),{method:'POST',body:JSON.stringify(data)});state.access=result.access;state.user=result.user;updateAccount();$('auth-dialog').close();e.target.reset();notice('Welcome, '+state.user.username+'.');}
  catch(err){$('auth-error').textContent=err.message;}finally{$('auth-submit').disabled=false;}
};
async function loadBookings(url='/api/bookings',append=false){
  if(needLogin())return;
  try{
    const data=await api(url);$('bookings-panel').hidden=false;$('explore-panel').hidden=true;$('seat-panel').hidden=true;closeSocket();
    $('bookings-nav').classList.add('active');$('explore-nav').classList.remove('active');
    if(!append)$('booking-list').replaceChildren();
    for(const b of data.results){const card=node('div',undefined,'booking-card'),info=node('div');info.append(node('strong',b.reservation.event_title),node('span','Seat '+b.reservation.seat_label+' · '+money(b.reservation.price,b.reservation.currency),'muted'),node('p',b.id,'fine'));card.append(info,node('span','CONFIRMED','confirmed'));$('booking-list').append(card);}
    if(!append&&!data.results.length)$('booking-list').append(node('p',"No bookings yet. Your next great night is waiting.",'muted'));
    state.bookingNext=data.next;$('more-bookings').hidden=!data.next;
  }catch(e){notice(e.message);}
}
function explore(){$('explore-panel').hidden=false;$('bookings-panel').hidden=true;$('explore-nav').classList.add('active');$('bookings-nav').classList.remove('active');}
$('bookings-nav').onclick=()=>loadBookings();$('back-events').onclick=explore;$('explore-nav').onclick=explore;
$('more-bookings').onclick=()=>loadBookings(state.bookingNext,true);
$('next-events').onclick=()=>loadEvents(state.eventNext);$('previous-events').onclick=()=>loadEvents(state.eventPrevious);
$('close-seats').onclick=()=>{$('seat-panel').hidden=true;closeSocket();};
window.addEventListener('focus',()=>{if(state.event&&!$('seat-panel').hidden)refreshSeats();updateReservation();});
setInterval(()=>{const r=state.reservation;if(r?.status==='HELD'){const left=Math.max(0,Math.ceil((Date.parse(r.expires_at)-Date.now()-state.offset)/1000));$('countdown').textContent=left?'Reserved for '+Math.floor(left/60)+':'+String(left%60).padStart(2,'0'):'Checking reservation…';}},1000);
setInterval(()=>{if(!document.hidden){if(state.event&&!$('seat-panel').hidden)refreshSeats();if(state.reservation?.status==='HELD')updateReservation();}},5000);
(async()=>{
  try{csrfToken=(await api('/api/auth/csrf')).csrfToken;await refreshAccess();const saved=sessionStorage.getItem('seatlock-reservation');if(saved&&state.user){state.reservation={id:saved};await updateReservation();
      if(state.reservation?.status==='HELD'){
        const snapshot=await api('/api/events/'+state.reservation.event_id+'/seats');await openEvent(snapshot.event);
      }}}catch(e){notice('Connection unavailable. Refresh to try again.');}
  await loadEvents();
})();
