#!/bin/bash
# live status in the terminal: shows zeroinput.html (written by zeroinput.py -web) without its HTML frame
watch -n1 -t 'grep -v "^<!DOCTYPE\|^<html\|^<head\|^<meta\|^<style\|^<body\|^<pre\|^</" '"$(dirname "$(readlink -f "$0")")"'/zeroinput.html'
