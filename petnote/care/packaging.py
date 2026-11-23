"""Explicit packaging metadata and receipt snapshots; never infer unopened packages."""
from decimal import Decimal
from .display_values import measure,number
from .inventory_ledger import MASS,VOLUME


def info(product):
    if not product or not product.package_label:return None
    return {'label':product.package_label,'amount':str(product.package_amount) if product.package_amount is not None else None,
            'unit':product.package_unit,'product_id':str(product.pk),'version':product.version,'stock_unit':product.stock_unit}


def capacity(pack):
    return measure(pack['amount'],pack['unit']).text if pack and pack.get('amount') and pack.get('unit') else ''


def description(pack):
    return '1'+pack['label']+' · '+capacity(pack) if capacity(pack) else ''


def base_amount(pack):
    if not pack:return None
    if pack['stock_unit']=='piece':
        return Decimal(pack['amount']) if pack.get('unit')=='piece' and pack.get('amount') else Decimal(1)
    if not pack.get('amount'):return None
    unit=pack.get('unit');target=pack['stock_unit']
    factors=MASS if target=='g' else VOLUME if target=='ml' else {}
    return Decimal(pack['amount'])*factors[unit] if unit in factors else None


def receipt_text(pack):
    if not pack or 'count' not in pack:return ''
    return number(pack['count'],0)+' '+pack['label']


def stock_context(product,item):
    pack=info(product);unit=product.stock_unit if product else item['unit']
    result={'label':'남은 개수' if unit=='piece' else '남은 내용물','package':description(pack),'equivalent':'','nominal':''}
    if not item:return result
    shown=measure(item['balance'],item['unit']);result.update(value=shown.value,unit=shown.unit)
    if unit=='piece' and pack and pack.get('unit')!='piece':
        result['unit']=pack['label']
        if capacity(pack):
            result['nominal']='포장 기준 총 '+measure(item['balance']*Decimal(pack['amount']),pack['unit']).text
    elif pack and base_amount(pack) and item['balance']>0:
        result['equivalent']='용량 환산 약 '+number(item['balance']/base_amount(pack),1)+' '+pack['label']+' 분량'
    return result
