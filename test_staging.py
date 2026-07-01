#!/usr/bin/python3
# -*- coding: utf-8 -*-
# unit test for input_power_staging.py — uses FakeDriver with the .stages list interface
import input_power_staging as staging

class FakeDriver:
	def __init__(self, did, stages, count, minp, maxp):
		self.id=did; self.stages=stages; self.count=count
		self.min_power=minp; self.max_power=maxp
	def group_capacity(self): return self.count*self.max_power

# 1 soyo base [1,2], 2 soyo group [2], 1 MP2 [2]
drivers = [
	FakeDriver('base',     [1,2], 1, 10,  900),
	FakeDriver('soyo_grp', [2],   2, 10,  900),
	FakeDriver('mp2',      [2],   1, 200, 2400),
]

ok, cap = staging.check_stage1_capacity(drivers, 666)
print('stage1 capacity %i W, covers threshold 666: %s\n' % (cap, ok))

def show(demand, stage):
	alloc = staging.distribute(demand, drivers, stage, 666)
	gt = {d.id: alloc[d.id]*d.count for d in drivers}
	served = sum(gt.values())
	print('demand %5i stage %i -> base %4i  soyo/ea %4i(x2=%4i)  mp2 %4i | served %5i miss %4i'
		% (demand, stage, alloc['base'], alloc['soyo_grp'], gt['soyo_grp'],
		   alloc['mp2'], served, demand-served))

print('--- stage 1 (only base) ---')
for d in [200, 600, 900, 1500]: show(d, 1)

print('\n--- stage 2 (equal share, saturation overflow) ---')
for d in [400, 800, 1200, 2400, 3600, 3700, 4000, 4500, 5100, 5700]: show(d, 2)

print('\n--- tiny demand (mp below min_power sleeps) ---')
for d in [50, 150, 300, 600]: show(d, 2)

print('\n--- handover: soyo [1], mp2 [2] ---')
drivers2 = [FakeDriver('soyo',[1],1,10,900), FakeDriver('mp2',[2],1,200,3000)]
def show2(demand, stage):
	a = staging.distribute(demand, drivers2, stage, 666)
	print('demand %5i stage %i -> soyo %4i  mp2 %4i' % (demand, stage, a['soyo'], a['mp2']))
for d in [200,600,900]: show2(d,1)
for d in [700,1500,3000,3500]: show2(d,2)

print('\n--- stage 2->1 cross-fade (fade-out lags fade-in, brief over-feed) ---')
fade_drv = [FakeDriver('s1',[1,2],1,10,900), FakeDriver('s2',[2],1,10,900),
            FakeDriver('s3',[2],1,10,900), FakeDriver('s4',[2],1,10,900)]
FADE = 5; DELAY = 2
for demand in [800, 400]:
	a2 = staging.distribute(demand, fade_drv, 2, 800)
	a1 = staging.distribute(demand, fade_drv, 1, 800)
	print('demand %i: from %s to %s' % (demand, a2, a1))
	total = FADE + DELAY
	for cnt in range(total, 0, -1):
		done = total - cnt + 1
		t = min(1.0, done / float(FADE))
		t_out = max(0, done - DELAY) / float(FADE)
		b = staging.fade_blend(a2, a1, t, fade_drv, t_out)
		print('  t=%.1f t_out=%.1f  %s  sum=%i' % (t, t_out, b, sum(b.values())))
