"""One catalogue and one stock ledger for food and explicit care-supply use."""
from decimal import Decimal
import json
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.urls import reverse

from . import inventory_ledger, services
from .forms import ProductForm
from .models import Activity, Product
from .settings8 import apply_once

FOOD=('meal','treat','both')
PRIVATE=('supp','medicine')
CATEGORIES=(('meal','사료'),('treat','간식'),('both','사료·간식 공용'),('supp','영양제'),('medicine','약·구충제'),('hygiene','위생용품'),('supply','생활 소모품'))
USES={'medication':('supp','medicine'),'hygiene':('hygiene',),'cleaning':('hygiene','supply')}
USE_MODULES=tuple(USES)
STOCK_MODULES=('inventory','meal','treat',*USE_MODULES)
USE_UNITS=(('g','g'),('mg','mg'),('kg','kg'),('ml','ml'),('l','L'),('piece','개'))


def can_see_private(member):return 'medication' in services.visible_modules(member)


def visible_products(member):
    q=Product.objects.filter(room=member.room)
    return q if can_see_private(member) else q.exclude(category__in=PRIVATE)


def hidden_stock_names(member):
    if can_see_private(member):return set()
    names=set(Product.objects.filter(room=member.room,category__in=PRIVATE).exclude(stock_item='').values_list('stock_item',flat=True))
    # Past private use remains private after renaming/disconnecting a current item.
    for row in Activity.objects.filter(room=member.room,module__in=('medication','inventory')).values('module','payload'):
        p=row['payload']
        if row['module']=='medication' and (p.get('_stock') or {}).get('item'):names.add(p['_stock']['item'])
        if row['module']=='inventory' and (p.get('_supply_private') or p.get('_supply',{}).get('category') in PRIVATE):
            if p.get('item'):names.add(p['item'])
    return names


def record_visible(member,record,names=None):
    if record.module not in services.visible_modules(member):return False
    if record.module!='inventory':return True
    names=names if names is not None else {inventory_ledger.normalized_item(s) for s in hidden_stock_names(member)}
    return inventory_ledger.normalized_item(record.payload.get('item')) not in names


def visible_records(member,query):
    names={inventory_ledger.normalized_item(s) for s in hidden_stock_names(member)}
    if not names:return query
    hidden=[row['pk'] for row in query.filter(module='inventory').values('pk','payload')
            if inventory_ledger.normalized_item(row['payload'].get('item')) in names]
    return query.exclude(pk__in=hidden)


def filter_rows(member,rows):
    names={inventory_ledger.normalized_item(s) for s in hidden_stock_names(member)}
    return [r for r in rows if record_visible(member,r,names)]


def require_record_access(member,record):
    if not record_visible(member,record):raise PermissionDenied('이 물품 기록을 볼 수 있는 권한이 없어요.')


class SupplyForm(ProductForm):
    track_stock=forms.BooleanField(label='이 물품의 재고 관리',required=False,help_text='실제 사용량을 남길 때만 차감해요. 등록만으로 재고나 사용 기록을 만들지 않아요.')
    class Meta(ProductForm.Meta):
        fields=['name','brand','category','notes',*ProductForm.Meta.fields[3:]]
        labels={**ProductForm.Meta.labels,'name':'물품 이름','category':'물품 종류','notes':'물품 메모 (선택)','active':'기록에서 선택할 수 있게 하기'}
        widgets={**ProductForm.Meta.widgets,'notes':forms.Textarea(attrs={'rows':2})}

    def __init__(self,*args,member,**kwargs):
        self.member=member
        super().__init__(*args,**kwargs)
        self.is_supply_form=True
        self.fields['category'].choices=CATEGORIES
        self.fields['request_token'].required=True
        for field in ('energy_basis','protein_kind','fat_kind'):self.fields[field].required=False
        self.fields['track_stock'].initial=bool(self.instance.stock_item)
        self.fields['stock_item'].label='재고 이름 (선택)'
        self.fields['stock_item'].help_text='비우면 물품 이름을 사용해요. 이미 있는 재고를 연결할 때는 같은 이름과 단위를 선택하세요.'
        self.fields['stock_item'].widget.attrs.update({'list':'stock-name-options','placeholder':'물품 이름과 동일'})
        self.fields['stock_unit'].label='재고 기준 단위'
        self.fields['stock_unit'].help_text='무게는 g, 부피는 ml, 개수는 개로 관리해요. 입력할 때 kg·L도 사용할 수 있어요.'
        self.fields['source'].required=False
        self.stock_names=sorted({a.payload.get('item','') for a in visible_records(member,Activity.objects.filter(room=member.room,module='inventory',voided=False)) if a.payload.get('item')})
        self.order_fields(['name','brand','category','notes','track_stock','stock_item','stock_unit','energy','energy_basis','grams_per_piece','protein','protein_kind','fat','fat_kind','source','active','version','request_token'])

    def clean(self):
        # Avoid demanding nutrition facts for non-food items, including forged hidden values.
        data=forms.ModelForm.clean(self)
        category=data.get('category')
        if category not in dict(CATEGORIES):return data
        if category in PRIVATE and not can_see_private(self.member):raise PermissionDenied()
        if self.instance.pk:
            old=Product.objects.filter(pk=self.instance.pk).values_list('category',flat=True).first()
            group=lambda c:'food' if c in FOOD else 'private' if c in PRIVATE else c
            used=Activity.objects.filter(room=self.member.room,payload__product_id=str(self.instance.pk)).exists()
            if used and group(old)!=group(category):self.add_error('category','사용 이력이 있는 물품은 다른 종류로 바꾸지 않아요. 새 물품으로 등록해 주세요.')
        if category not in FOOD:
            for key in ('energy','protein','fat','grams_per_piece'):data[key]=None
            data.update(energy_basis='100g',protein_kind='min',fat_kind='min')
        else:
            data['energy_basis']=data.get('energy_basis') or '100g'
            data['protein_kind']=data.get('protein_kind') or 'min'
            data['fat_kind']=data.get('fat_kind') or 'min'
            for key in ('energy','protein','fat','grams_per_piece'):
                n=data.get(key)
                if n is not None and (n<0 or (key=='grams_per_piece' and n<=0)):
                    self.add_error(key,'올바른 양수를 입력해 주세요.' if key=='grams_per_piece' else '0 이상으로 입력해 주세요.')
            for key in ('protein','fat'):
                if data.get(key) is not None and data[key]>100:self.add_error(key,'100g당 성분량은 100g을 넘을 수 없어요.')
            if any(data.get(k) is not None for k in ('energy','protein','fat')) and not str(data.get('source','')).strip():
                self.add_error('source','영양정보를 확인한 출처를 적어 주세요.')
        if data.get('track_stock'):
            data['stock_item']=' '.join((data.get('stock_item') or data.get('name','')).split())
            if not data.get('stock_unit'):self.add_error('stock_unit','재고의 기준 단위를 선택해 주세요.')
        else:data['stock_item']='';data['stock_unit']=''
        if data.get('stock_item'):
            normal=inventory_ledger.normalized_item(data['stock_item'])
            for other in Product.objects.filter(room=self.member.room).exclude(pk=self.instance.pk).exclude(stock_item=''):
                if inventory_ledger.normalized_item(other.stock_item)==normal and other.stock_unit==data.get('stock_unit'):
                    if (category in PRIVATE)!=(other.category in PRIVATE):
                        self.add_error('stock_item','민감한 물품과 일반 물품의 재고는 별도 이름으로 관리해 주세요.')
        return data


def save_supply(member,form,instance=None):
    services.require_manager(member)
    if instance and not visible_products(member).filter(pk=instance.pk).exists():raise PermissionDenied()
    def save(actor):
        services.require_manager(actor)
        item=services.save_product(actor,form,instance.pk if instance else None,_receipt=True)
        return {'id':str(item.pk),'version':item.version,'changed':True}
    return apply_once(member,form,'supply.save',save)


def install_record_fields(form):
    """Attach optional product use to existing record forms; never infer a dose."""
    key=form.spec.key
    if key not in USES and key!='inventory':return
    original=form.instance.payload.get('product_id') if form.instance else None
    q=visible_products(form.member)
    if key in USES:q=q.filter(category__in=USES[key])
    if key=='inventory':q=q.exclude(stock_item='')
    q=q.filter(Q(active=True)|Q(pk=original))
    form.fields['product']=forms.ModelChoiceField(label='등록한 물품 (선택)',queryset=q.order_by('name'),required=False,empty_label='직접 입력',help_text='같은 물품 정보를 다시 입력하지 않아도 돼요.')
    form.fields['product'].widget.attrs['data-supply-module']=key
    form.fields['product'].widget.attrs['data-supply-options']=json.dumps([{'id':str(p.pk),'name':p.name,'stock_item':p.stock_item,'stock_unit':p.stock_unit} for p in q],ensure_ascii=False)
    if original and 'product' not in form.initial:form.initial['product']=original
    selected=form.data.get('product') if form.is_bound else form.initial.get('product')
    product=q.filter(pk=selected).first() if selected and _uuid(selected) else None
    if product and form.is_bound:
        data=form.data.copy()
        if key=='inventory' and not data.get('item'):data['item']=product.stock_item
        if key=='medication' and not data.get('routine_title'):data['routine_title']=product.name
        form.data=data
    if product and not form.is_bound:
        if key=='inventory':form.initial.update(item=product.stock_item,unit=product.stock_unit)
        elif key=='medication':form.initial.setdefault('routine_title',product.name)
    if key in USES:
        form.fields['used_quantity']=forms.DecimalField(label='실제 사용량 (선택)',max_digits=12,decimal_places=3,min_value=Decimal('0.001'),required=False,help_text='재고에서 실제로 꺼내 쓴 양이에요.')
        form.fields['used_unit']=forms.ChoiceField(label='사용량 단위',choices=USE_UNITS,required=False)
        form.initial.setdefault('used_unit',product.stock_unit if product and product.stock_unit else 'piece')
        if form.instance and form.instance.payload.get('_stock'):
            form.fields['refresh_stock']=forms.BooleanField(label='현재 물품의 재고 연결로 변경',required=False)
    # Put the product selector near the beginning, followed by the actual care fields.
    order=list(form.fields);order.remove('product');pos=order.index('status')+1
    order.insert(pos,'product');form.order_fields(order)


def _uuid(raw):
    import uuid
    try:uuid.UUID(str(raw));return True
    except (ValueError,TypeError,AttributeError):return False


def clean_record(form,data):
    key=form.spec.key;product=data.get('product')
    if key not in USES and key!='inventory':return data
    if product:
        if product.room_id!=form.room.pk:raise ValidationError('이 공간에 등록한 물품을 선택해 주세요.')
        if key=='inventory':
            if not product.stock_item:form.add_error('product','먼저 물품의 재고 관리를 켜 주세요.')
            else:
                data['item']=product.stock_item
                allowed={'g':('g','kg','oz'),'ml':('ml','l'),'piece':('piece',)}.get(product.stock_unit,())
                if data.get('unit') not in allowed:form.add_error('unit','연결된 재고와 같은 종류의 단위를 선택해 주세요.')
    if key=='inventory':
        names={inventory_ledger.normalized_item(s) for s in hidden_stock_names(form.member)}
        if inventory_ledger.normalized_item(data.get('item')) in names:raise PermissionDenied()
    if key in USES:
        amount=data.get('used_quantity')
        if amount is not None and not product:form.add_error('product','사용량을 연결할 물품을 선택해 주세요.')
        if amount is not None and not data.get('used_unit'):form.add_error('used_unit','사용량 단위를 선택해 주세요.')
        if key=='medication' and data.get('result')=='skipped' and amount is not None:form.add_error('used_quantity','실행하지 않은 기록에는 사용량을 넣지 않아요.')
    return data


def record_payload(form,payload):
    key=form.spec.key
    if key not in USES and key!='inventory':return payload
    data=form.cleaned_data;product=data.get('product')
    payload['product_id']=str(product.pk) if product else None
    if key=='inventory':
        private_names={inventory_ledger.normalized_item(x) for x in Product.objects.filter(room=form.room,category__in=PRIVATE).exclude(stock_item='').values_list('stock_item',flat=True)}
        private=inventory_ledger.normalized_item(data.get('item')) in private_names
        if private or (form.instance and form.instance.payload.get('_supply_private')):payload['_supply_private']=True
        if product:payload['_supply']={'id':str(product.pk),'name':product.name,'brand':product.brand,'category':product.category,'version':product.version}
    if key in USES:
        amount=data.get('used_quantity');payload['used_quantity']=str(amount) if amount is not None else None
        payload['used_unit']=data.get('used_unit') or None
    return payload


def apply_usage(member,module,data,payload,activity):
    """Resolve a use snapshot once; changed products do not rewrite historical bindings."""
    if module not in USES:return payload
    product=data.get('product')
    same=bool(activity and activity.payload.get('product_id')==payload.get('product_id'))
    if product:
        product=Product.objects.get(room=member.room,pk=product.pk)
        if product.category not in USES[module] or (not product.active and not same):raise ValidationError('사용할 수 있는 물품을 다시 선택해 주세요.')
        if product.category in PRIVATE and not can_see_private(member):raise PermissionDenied()
        snapshot=activity.payload.get('_supply') if same else None
        payload['_supply']=snapshot or {'id':str(product.pk),'name':product.name,'brand':product.brand,'category':product.category,'version':product.version}
    else:payload.pop('_supply',None)
    binding=activity.payload.get('_stock') if same and not data.get('refresh_stock') else inventory_ledger.product_binding(product)
    if module=='medication' and data.get('result')=='skipped':binding=None
    amount=payload.get('used_quantity');unit=payload.get('used_unit')
    if unit=='mg' and amount is not None:amount=str(Decimal(amount)/1000);unit='g'
    effect=inventory_ledger.food_effect({'quantity':amount,'unit':unit},binding)
    if effect:
        effect['basis']='used';payload['_stock']=effect
    else:payload.pop('_stock',None)
    return payload


def stock_view(member):
    rows=inventory_ledger.records_for(member.room)
    result=inventory_ledger.ledger(rows)
    hidden={inventory_ledger.normalized_item(s) for s in hidden_stock_names(member)}
    result['items']=[x for x in result['items'] if x['key'][0] not in hidden]
    result['issues']=[x for x in result['issues'] if visible_records(member,Activity.objects.filter(pk=x['id'])).exists()]
    visible=set(services.visible_modules(member))
    for item in result['items']:
        item['display_value'],item['display_unit']=inventory_ledger.display_quantity(item['balance'],item['unit'],prefer_large=item['unit'] in ('g','ml'))
        item['url']=reverse('records',args=[member.room_id])+'?'+urlencode({'module':'inventory','q':item['name']})
        item['recent_events']=[dict(e,url=reverse('record_detail',args=[member.room_id,e['id']])) for e in reversed(item['events']) if e['module'] in visible][:8]
    return result
