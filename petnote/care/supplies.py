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
from .profile_settings import apply_once

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
    allowed = set(services.visible_modules(member))
    hidden = None
    visible = []
    for row in rows:
        if row.module not in allowed:
            continue
        if row.module == "inventory":
            if hidden is None:
                hidden = {inventory_ledger.normalized_item(name) for name in hidden_stock_names(member)}
            if inventory_ledger.normalized_item(row.payload.get("item")) in hidden:
                continue
        visible.append(row)
    return visible


def require_record_access(member,record):
    if not record_visible(member,record):raise PermissionDenied('이 물품 기록을 볼 수 있는 권한이 없어요.')


class SupplyForm(ProductForm):
    track_stock=forms.BooleanField(label='이 물품의 재고 관리',required=False,help_text='실제 사용량을 남길 때만 차감해요. 등록만으로 재고나 사용 기록을 만들지 않아요.')
    class Meta(ProductForm.Meta):
        fields=['name','brand','category','notes','package_label','package_amount','package_unit',*ProductForm.Meta.fields[3:]]
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
        self.fields['package_label'].label='개수 이름 (선택)'
        self.fields['package_label'].help_text='통·봉지·장처럼 실제로 세는 이름을 골라요.'
        self.fields['package_amount'].label='하나에 든 양 (선택)'
        self.fields['package_amount'].help_text='포장에 적힌 전체 용량이에요. 간식 한 조각의 무게와 구분해요.'
        self.fields['package_amount'].widget.attrs.update(min='0.001',step='0.001')
        self.fields['package_unit'].label='포장 용량 단위'
        self.fields['stock_unit'].label='남은 재고를 관리할 기준'
        self.fields['stock_unit'].choices=[('','선택'),('g','내용물 무게 (g)'),('ml','내용물 부피 (ml)'),('piece','물품 개수')]
        self.fields['stock_unit'].help_text='조금씩 덜어 쓰면 무게·부피, 통째로 쓰면 개수로 관리해요. 입고할 때는 등록한 포장 개수로도 적을 수 있어요.'
        self.fields['source'].required=False
        self.stock_names=sorted({a.payload.get('item','') for a in visible_records(member,Activity.objects.filter(room=member.room,module='inventory',voided=False)) if a.payload.get('item')})
        self.order_fields(['name','brand','category','notes','package_label','package_amount','package_unit','track_stock','stock_item','stock_unit','energy','energy_basis','grams_per_piece','protein','protein_kind','fat','fat_kind','source','active','version','request_token'])

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
        amount=data.get('package_amount');unit=data.get('package_unit');label=data.get('package_label')
        if amount is not None and amount<=0:self.add_error('package_amount','포장 용량은 0보다 크게 입력해 주세요.')
        if bool(amount is not None)!=bool(unit):self.add_error('package_amount','포장 용량과 단위를 함께 입력해 주세요.')
        if amount is not None and not label:self.add_error('package_label','포장을 세는 이름도 골라 주세요.')
        if unit=='piece' and amount is not None and amount%1:self.add_error('package_amount','포장 안의 개수는 정수로 적어 주세요.')
        if category in FOOD and data.get('stock_unit')=='piece' and label not in ('',None,'개') and unit in ('g','kg','ml','l'):
            self.add_error('stock_unit','사료·간식 포장은 내용물 무게·부피로 관리해 주세요. 급여할 한 조각과 포장 한 개를 구분해요.')
        if data.get('track_stock') and amount is not None:
            allowed={'g':('g','kg'),'ml':('ml','l'),'piece':('g','kg','ml','l','piece')}.get(data.get('stock_unit'),())
            if unit not in allowed:self.add_error('package_unit','남은 재고와 같은 종류의 용량을 선택해 주세요.')
        if data.get('track_stock'):
            data['stock_item']=' '.join((data.get('stock_item') or data.get('name','')).split())
            if not data.get('stock_unit'):self.add_error('stock_unit','재고의 기준 단위를 선택해 주세요.')
        else:data['stock_item']='';data['stock_unit']=''
        if self.instance.pk and data.get('track_stock'):
            old=Product.objects.filter(pk=self.instance.pk).first()
            if old and old.stock_item and old.stock_unit!=data.get('stock_unit') and inventory_ledger.records_for(self.member.room).filter(module='inventory',payload__item=old.stock_item).exists():
                self.add_error('stock_unit','기존 재고 기록의 단위는 유지해요. 다른 기준으로 관리하려면 새 물품과 재고를 등록해 주세요.')
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
    from . import packaging
    form.fields['product'].widget.attrs['data-supply-options']=json.dumps([{'id':str(p.pk),'name':p.name,'stock_item':p.stock_item,'stock_unit':p.stock_unit,'package':packaging.info(p)} for p in q],ensure_ascii=False)
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
    if key=='inventory':
        form.fields['unit'].choices=list(form.fields['unit'].choices)+[('package','포장 개수 (등록한 용량 기준)')]
        form.fields['package_version']=forms.IntegerField(required=False,widget=forms.HiddenInput)
        pack=packaging.info(product)
        previous=form.instance
        if previous is None and form.is_bound and _uuid(form.data.get('client_key')):
            previous=Activity.objects.filter(room=form.room,author=form.member,module='inventory',client_key=form.data['client_key']).first()
        oldpack=previous.payload.get('_package') if previous else None
        if oldpack and product and oldpack['product_id']==str(product.pk):
            pack=oldpack
            if not form.is_bound:
                form.initial.update(quantity=oldpack['count'],unit='package',minimum=oldpack.get('minimum_count'))
        if oldpack and pack is oldpack:
            form.package_previous_payload=previous.payload
            choices=json.loads(form.fields['product'].widget.attrs['data-supply-options'])
            for choice in choices:
                if choice['id']==pack['product_id']:choice['package']=pack
            form.fields['product'].widget.attrs['data-supply-options']=json.dumps(choices,ensure_ascii=False)
        form.initial['package_version']=pack['version'] if pack else None
        form.fields['quantity'].help_text='포장 개수를 선택하면 등록한 용량으로 환산해 한 번만 재고에 반영해요. 개봉한 잔량은 무게·부피로 직접 적어 주세요.'
        form.fields['minimum'].help_text='위에서 고른 단위 기준이에요. 포장 개수라면 포장 수 기준으로 환산해요.'
        form.package_snapshot=pack
    if key in USES:
        form.fields['used_quantity']=forms.DecimalField(label='실제 사용량 (선택)',max_digits=12,decimal_places=3,min_value=Decimal('0.001'),required=False,help_text='재고에서 실제로 꺼내 쓴 양이에요.')
        form.fields['used_unit']=forms.ChoiceField(label='사용량 단위',choices=USE_UNITS,required=False)
        form.initial.setdefault('used_unit',product.stock_unit if product and product.stock_unit else 'piece')
        if product and product.stock_unit=='piece' and product.package_label and product.package_unit!='piece':
            form.fields['used_unit'].choices=[(k,product.package_label if k=='piece' else label) for k,label in USE_UNITS]
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
                if data.get('unit')=='package':
                    from .packaging import base_amount
                    pack=form.package_snapshot
                    factor=base_amount(pack)
                    if not factor:
                        form.add_error('unit','물품 수정에서 포장 이름과 용량을 먼저 등록해 주세요.')
                    elif data.get('package_version')!=pack['version']:
                        form.add_error('unit','포장 정보가 바뀌었어요. 물품을 다시 선택해 용량을 확인해 주세요.')
                    elif data.get('quantity') is not None:
                        for field in ('quantity','minimum'):
                            if data.get(field) is not None and data[field]%1:form.add_error(field,'포장 개수는 정수로 입력해 주세요. 남은 내용물은 무게·부피로 적어 주세요.')
                        if data.get('quantity') is not None:
                            if getattr(form,'package_previous_payload',None):data['item']=form.package_previous_payload['item']
                            form.package_receipt={**pack,'count':str(data['quantity']),'minimum_count':str(data['minimum']) if data.get('minimum') is not None else None}
                            data['quantity']*=factor
                            if data.get('minimum') is not None:data['minimum']*=factor
                            data['unit']=pack['stock_unit']
                allowed={'g':('g','kg','oz'),'ml':('ml','l'),'piece':('piece',)}.get(product.stock_unit,())
                if data.get('unit') not in allowed:form.add_error('unit','연결된 재고와 같은 종류의 단위를 선택해 주세요.')
    if key=='inventory':
        if data.get('unit')=='package' and not product:form.add_error('product','포장 용량을 등록한 물품을 선택해 주세요.')
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
        if getattr(form,'package_receipt',None):payload['_package']=form.package_receipt
        private_names={inventory_ledger.normalized_item(x) for x in Product.objects.filter(room=form.room,category__in=PRIVATE).exclude(stock_item='').values_list('stock_item',flat=True)}
        private=inventory_ledger.normalized_item(data.get('item')) in private_names
        if private or (form.instance and form.instance.payload.get('_supply_private')):payload['_supply_private']=True
        if product:payload['_supply']={'id':str(product.pk),'name':product.name,'brand':product.brand,'category':product.category,'version':product.version}
        if getattr(form,'package_receipt',None) and getattr(form,'package_previous_payload',None):
            payload['_supply']=form.package_previous_payload.get('_supply',payload.get('_supply'))
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
    products=list(visible_products(member).exclude(stock_item=''))
    for item in result['items']:
        from .reporting.display_values import measure
        shown=measure(item['balance'],item['unit'])
        item['display_value'],item['display_unit']=shown.value,shown.unit
        from .packaging import stock_context
        matches=[p for p in products if (inventory_ledger.normalized_item(p.stock_item),p.stock_unit)==item['key']]
        item['context']=stock_context(matches[0] if len(matches)==1 else None,item)
        item['url']=reverse('records',args=[member.room_id])+'?'+urlencode({'module':'inventory','q':item['name']})
        item['recent_events']=[dict(e,url=reverse('record_detail',args=[member.room_id,e['id']])) for e in reversed(item['events']) if e['module'] in visible][:8]
    return result
