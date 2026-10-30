document.addEventListener('DOMContentLoaded',()=>{
  const themeButton=document.querySelector('.theme-toggle');
  if(themeButton){
    const savedTheme=document.documentElement.dataset.theme;
    const theme=savedTheme||(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
    function applyTheme(value){document.documentElement.dataset.theme=value;themeButton.textContent=value==='dark'?'☀ Light mode':'☾ Dark mode';themeButton.setAttribute('aria-label',value==='dark'?'Switch to light mode':'Switch to dark mode');themeButton.setAttribute('aria-pressed',String(value==='dark'))}
    applyTheme(theme);
    themeButton.addEventListener('click',()=>{const nextTheme=document.documentElement.dataset.theme==='dark'?'light':'dark';applyTheme(nextTheme);try{localStorage.setItem('mediledger-theme',nextTheme)}catch(error){console.warn('Could not save theme preference.',error)}});
  }

  const search=document.querySelector('#batch-search');
  if(search)search.addEventListener('input',()=>{const term=search.value.toLowerCase();document.querySelectorAll('#batch-table tbody tr').forEach(row=>{row.hidden=!row.dataset.search.includes(term)})});

  const form=document.querySelector('#bill-form');
  if(!form)return;
  const cart=new Map();
  const money=value=>'₹'+Number(value).toFixed(2);
  const customerName=document.querySelector('#customer-name');
  const customerPhone=document.querySelector('#customer-phone');
  const historyBox=document.querySelector('#customer-history');
  const submitButton=document.querySelector('#submit-bill');
  let hasUnpaid=false, pendingBillId=null, lookupPending=false, lookupFailed=false, lookupTimer=null, lookupSequence=0;

  function makeCell(row,value){const td=document.createElement('td');td.textContent=value;row.appendChild(td);return td}
  function renderHistory(data){
    historyBox.replaceChildren();historyBox.classList.remove('is-blocked','is-clear','is-error');
    const header=document.createElement('div');header.className='history-heading';
    const icon=document.createElement('span');icon.className='history-icon';icon.textContent=data.found?'↻':'✓';header.appendChild(icon);
    const title=document.createElement('span');const strong=document.createElement('strong');strong.textContent=data.found?'Previous customer bills':'No previous customer record';title.appendChild(strong);
    const small=document.createElement('small');small.textContent=data.found?'Previous invoices for this phone number are listed below.':'This phone number has no saved bill history.';title.appendChild(small);header.appendChild(title);historyBox.appendChild(header);
    if(data.previous_bills?.length){
      const wrap=document.createElement('div');wrap.className='table-wrap history-table';const table=document.createElement('table');
      const thead=document.createElement('thead');const hr=document.createElement('tr');['Bill','Date','Total','Paid','Balance','Status'].forEach(v=>makeCell(hr,v));thead.appendChild(hr);table.appendChild(thead);
      const tbody=document.createElement('tbody');
      data.previous_bills.forEach(bill=>{const tr=document.createElement('tr');
        const linkCell=document.createElement('td');const link=document.createElement('a');link.href=form.dataset.billUrl.replace(/0$/,String(bill.id));link.textContent=bill.bill_number;linkCell.appendChild(link);tr.appendChild(linkCell);
        makeCell(tr,bill.created_at);makeCell(tr,money(bill.total));makeCell(tr,money(bill.paid));makeCell(tr,money(bill.due));
        const statusCell=document.createElement('td');const badge=document.createElement('span');badge.className='pill '+(bill.payment_status==='paid'?'success':bill.payment_status==='partial'?'warning':'danger');badge.textContent=bill.payment_status.charAt(0).toUpperCase()+bill.payment_status.slice(1);statusCell.appendChild(badge);tr.appendChild(statusCell);tbody.appendChild(tr);
      });table.appendChild(tbody);wrap.appendChild(table);historyBox.appendChild(wrap);
    }
    if(data.has_unpaid){historyBox.classList.add('is-blocked');const p=document.createElement('p');p.className='history-warning';p.textContent='New billing is blocked. Use “Open previous bill” below to settle the oldest outstanding invoice.';historyBox.appendChild(p);}
    else{historyBox.classList.add('is-clear');if(data.found){const p=document.createElement('p');p.className='history-clear';p.textContent='No outstanding balance found. You can continue with this bill.';historyBox.appendChild(p)}}
  }
  function setLookupMessage(message,error=false){historyBox.replaceChildren();historyBox.classList.remove('is-blocked','is-clear');historyBox.classList.toggle('is-error',error);const p=document.createElement('p');p.className=error?'history-error':'muted';p.textContent=message;historyBox.appendChild(p)}
  async function lookupCustomer(){
    const rawPhone=customerPhone.value;
    const name=customerName.value.trim().toLowerCase();
    if(name==='walk-in customer'&&!rawPhone){hasUnpaid=false;pendingBillId=null;lookupPending=false;lookupFailed=false;setLookupMessage('Walk-in customer: no saved phone history to check.');render();return}
    if(!rawPhone){hasUnpaid=false;pendingBillId=null;lookupPending=false;lookupFailed=false;setLookupMessage('Enter the customer phone number to look up previous bills.');render();return}
    if(!/^[6-9]\d{9}$/.test(rawPhone)){hasUnpaid=false;pendingBillId=null;lookupPending=false;lookupFailed=true;setLookupMessage('Enter a valid 10-digit mobile number starting with 6–9 to complete the customer check.',true);render();return}
    const sequence=++lookupSequence;lookupPending=true;lookupFailed=false;hasUnpaid=false;pendingBillId=null;setLookupMessage('Checking previous bills and outstanding balances…');render();
    try{
      const response=await fetch(form.dataset.customerLookupUrl+'?phone='+encodeURIComponent(rawPhone),{headers:{'Accept':'application/json'}});
      const data=await response.json();if(sequence!==lookupSequence)return;
      if(!response.ok)throw new Error(data.error||'Customer lookup failed.');
      hasUnpaid=Boolean(data.has_unpaid);pendingBillId=data.previous_bills?.filter(bill=>Number(bill.due)>0).sort((a,b)=>a.id-b.id)[0]?.id||null;lookupPending=false;lookupFailed=false;renderHistory(data);render();
    }catch(error){if(sequence!==lookupSequence)return;lookupPending=false;lookupFailed=true;hasUnpaid=false;pendingBillId=null;setLookupMessage(error.message+' New billing is paused until the check succeeds.',true);render()}
  }
  function scheduleLookup(){clearTimeout(lookupTimer);lookupSequence++;hasUnpaid=false;pendingBillId=null;lookupPending=true;lookupFailed=false;render();lookupTimer=setTimeout(lookupCustomer,350)}
  customerPhone.addEventListener('input',scheduleLookup);
  customerName.addEventListener('change',scheduleLookup);
  if(customerPhone.value.trim())lookupCustomer();

  function render(){
    const body=document.querySelector('#bill-lines');body.replaceChildren();let subtotal=0,tax=0;
    for(const [id,item] of cart){
      const base=item.price*item.quantity;const lineTax=base*item.gst/100;subtotal+=base;tax+=lineTax;
      const tr=document.createElement('tr');makeCell(tr,item.name+' · '+item.batch);makeCell(tr,String(item.quantity));makeCell(tr,money(item.price));makeCell(tr,money(base));
      const actionCell=document.createElement('td');const remove=document.createElement('button');remove.type='button';remove.className='btn btn-small btn-danger';remove.textContent='Remove';remove.addEventListener('click',()=>{cart.delete(id);render()});actionCell.appendChild(remove);tr.appendChild(actionCell);body.appendChild(tr);
    }
    if(!cart.size){const tr=document.createElement('tr');const td=makeCell(tr,'No items added yet.');td.colSpan=5;td.className='empty';body.appendChild(tr)}
    const discountInput=document.querySelector('#discount');const discount=Number(discountInput.value||0);
    document.querySelector('#subtotal').textContent=money(subtotal);document.querySelector('#tax').textContent=money(tax);document.querySelector('#total').textContent=money(Math.max(0,subtotal+tax-discount));
    document.querySelector('#items-json').value=JSON.stringify([...cart].map(([batch_id,item])=>({batch_id:Number(batch_id),quantity:item.quantity})));
    const customerRequiresLookup=customerName.value.trim().toLowerCase()!=='walk-in customer'||Boolean(customerPhone.value.trim());
    const identityBlocked=customerRequiresLookup&&(!customerPhone.value.trim()||lookupPending||lookupFailed||hasUnpaid);
    submitButton.textContent=hasUnpaid?'Open previous bill':'Create unpaid bill';
    submitButton.disabled=lookupPending||lookupFailed||(hasUnpaid?!pendingBillId:!cart.size||!Number.isFinite(discount)||discount<0||discount>subtotal+tax||identityBlocked);
  }
  document.querySelectorAll('.add-line').forEach(button=>button.addEventListener('click',()=>{
    const row=button.closest('tr');const quantity=Number(row.querySelector('.qty-input').value);const stock=Number(button.dataset.stock);const id=button.dataset.id;
    if(!Number.isInteger(quantity)||quantity<1||quantity>stock){alert('Enter a valid quantity within available stock.');return}
    const previous=cart.get(id);const next=(previous?.quantity||0)+quantity;if(next>stock){alert('Quantity exceeds available stock.');return}
    cart.set(id,{name:button.dataset.name,batch:button.dataset.batch,price:Number(button.dataset.price),gst:Number(button.dataset.gst),quantity:next});render();
  }));
  document.querySelector('#discount').addEventListener('input',render);
  form.addEventListener('submit',event=>{
    if(hasUnpaid&&pendingBillId){event.preventDefault();window.location.assign(form.dataset.billUrl.replace(/0$/,String(pendingBillId)))}
    else if(!cart.size||submitButton.disabled){event.preventDefault();if(lookupPending||lookupFailed)alert('Wait for the customer bill check to complete before creating a bill.');else alert('Add at least one medicine and enter valid bill details.');}
    else{submitButton.disabled=true;submitButton.textContent='Creating bill…'}
  });
  render();
});
