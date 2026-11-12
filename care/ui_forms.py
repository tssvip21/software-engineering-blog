"""HTML fragments for in-place input, with existing validation and mutation services."""
from django.http import JsonResponse
from django.shortcuts import render

def in_dialog(request):
    return request.headers.get("X-Taily-Dialog") == "1"

def render_input(request, template, context, status=200):
    if in_dialog(request):
        form=context.get("form")
        if request.method=="POST" and form is not None and form.errors and status==200:
            status=422
        response=render(request,"care7/_input_form.html",{**context,"in_dialog":True,"form_action":request.get_full_path()},status=status)
        response["X-Taily-Fragment"]="input-form"
        response["Cache-Control"]="no-store"
        return response
    return render(request,template,context,status=status)

def saved(request, message, url, **result):
    response=JsonResponse({"ok":True,"message":message,"url":url,**result})
    response["Cache-Control"]="no-store"
    return response
