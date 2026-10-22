document.addEventListener('DOMContentLoaded',()=>{
const themeButton=document.querySelector('.theme-toggle');
if(themeButton){
const savedTheme=document.documentElement.dataset.theme;
const theme=savedTheme||(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
function applyTheme(value){document.documentElement.dataset.theme=value;themeButton.textContent=value==='dark'?'☀ Light mode':'☾ Dark mode';themeButton.setAttribute('aria-label',value==='dark'?'Switch to light mode':'Switch to dark mode');themeButton.setAttribute('aria-pressed',String(value==='dark'))}
applyTheme(theme);
themeButton.addEventListener('click',()=>{
const nextTheme=document.documentElement.dataset.theme==='dark'?'light':'dark';
applyTheme(nextTheme);
try{localStorage.setItem('mediledger-theme',nextTheme)}catch(error){console.warn('Could not save theme preference.',error)}
});
}
const search=document.querySelector('#batch-search');
if(search)search.addEventListener('input',()=>{const q=search.value.toLowerCase();document.querySelectorAll('#batch-table tbody tr').forEach(r=>r.hidden=!r.dataset.search.includes(q));});
const form=document.querySelector('#bill-form');if(!form)return;const cart=new Map();const money=n=>'₹'+Number(n).toFixed(2);
function render(){const body=document.querySelector('#bill-lines');body.innerHTML='';let subtotal=0,tax=0;
for(const [id,v] of cart){const base=v.price*v.quantity,t=base*v.gst/100;subtotal+=base;tax+=t;const tr=document.createElement('tr');[v.name+' · '+v.batch,v.quantity,money(v.price),money(base)].forEach(txt=>{const td=document.createElement('td');td.textContent=txt;tr.appendChild(td)});const td=document.createElement('td'),b=document.createElement('button');b.type='button';b.className='btn btn-small btn-danger';b.textContent='Remove';b.onclick=()=>{cart.delete(id);render()};td.appendChild(b);tr.appendChild(td);body.appendChild(tr)}
if(!cart.size)body.innerHTML='<tr><td colspan="5" class="empty">No items added yet.</td></tr>';const discount=Math.max(0,Number(document.querySelector('#discount').value||0));
document.querySelector('#subtotal').textContent=money(subtotal);document.querySelector('#tax').textContent=money(tax);document.querySelector('#total').textContent=money(Math.max(0,subtotal+tax-discount));
document.querySelector('#items-json').value=JSON.stringify([...cart].map(([batch_id,v])=>({batch_id:Number(batch_id),quantity:v.quantity})));document.querySelector('#submit-bill').disabled=!cart.size||discount>subtotal+tax}
document.querySelectorAll('.add-line').forEach(b=>b.addEventListener('click',()=>{const row=b.closest('tr'),qty=Number(row.querySelector('.qty-input').value),stock=Number(b.dataset.stock),id=b.dataset.id;if(!Number.isInteger(qty)||qty<1||qty>stock){alert('Enter a valid quantity within available stock.');return}const old=cart.get(id),next=(old?.quantity||0)+qty;if(next>stock){alert('Quantity exceeds available stock.');return}cart.set(id,{name:b.dataset.name,batch:b.dataset.batch,price:Number(b.dataset.price),gst:Number(b.dataset.gst),quantity:next});render()}));
document.querySelector('#discount').addEventListener('input',render);form.addEventListener('submit',e=>{if(!cart.size){e.preventDefault();alert('Add at least one medicine.')}});render();
});
