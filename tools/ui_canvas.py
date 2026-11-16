"""Original scalable Chinese Canvas UI; all controls are native HUD hitboxes."""
from __future__ import annotations

import math
import unreal
from blueprint_runtime import _call, _event, _exec, _graph, _value, _wire, _compile
from combat_v2 import _math, _get, _connect, _variables, _function

INK = '(R=0.023,G=0.029,B=0.035,A=0.94)'
PANEL = '(R=0.032,G=0.041,B=0.049,A=0.93)'
RAISED = '(R=0.064,G=0.077,B=0.086,A=1)'
HOVER = '(R=0.12,G=0.14,B=0.15,A=1)'
WHITE = '(R=0.94,G=0.93,B=0.88,A=1)'
MUTED = '(R=0.55,G=0.61,B=0.63,A=1)'
LINE = '(R=0.16,G=0.20,B=0.22,A=1)'
AMBER = '(R=1,G=0.53,B=0.14,A=1)'
AMBER_BG = '(R=0.19,G=0.095,B=0.036,A=0.97)'
GREEN = '(R=0.38,G=0.79,B=0.64,A=1)'


class Painter:
    def __init__(self, editor, source, scale, ox, oy, fonts):
        self.e, self.tail, self.scale, self.ox, self.oy, self.fonts = editor, source, scale, ox, oy, fonts

    def mul(self, v):
        return _math(self.e, 'Multiply_DoubleDouble', A=self.scale, B=v)

    def x(self, v):
        return _math(self.e, 'Add_DoubleDouble', A=self.ox, B=self.mul(v))

    def y(self, v):
        return _math(self.e, 'Add_DoubleDouble', A=self.oy, B=self.mul(v))

    def node(self, fn, **pins):
        node = _call(self.e, fn)
        for key, val in pins.items():
            _connect(val, node, key)
        _exec(self.tail, node)
        self.tail = node
        return node

    def rect(self, x, y, w, h, color=PANEL):
        self.node('HUD.DrawRect', ScreenX=self.x(x), ScreenY=self.y(y),
                  ScreenW=self.mul(w), ScreenH=self.mul(h), RectColor=color)

    def line(self, x1, y1, x2, y2, color=LINE, width=1):
        self.node('HUD.DrawLine', StartScreenX=self.x(x1), StartScreenY=self.y(y1),
                  EndScreenX=self.x(x2), EndScreenY=self.y(y2), LineColor=color,
                  LineThickness=self.mul(width))

    def text(self, text, x, y, size=16, color=WHITE, bold=False):
        self.node('HUD.DrawText', Text=text, ScreenX=self.x(x), ScreenY=self.y(y),
                  TextColor=color, Font=self.fonts['bold' if bold else 'regular'],
                  # Canvas uses Slate's 96-DPI font rasterizer: 24pt = 32px.
                  Scale=self.mul(size / 32.0), bScalePosition='false')

    def hovered(self, action):
        return _math(self.e, 'EqualEqual_NameName', A=_get(self.e, 'HoveredAction'), B=action)

    def choose_color(self, condition, yes, no):
        return _math(self.e, 'SelectColor', A=yes, B=no, bPickA=condition)

    def hit(self, action, x, y, w, h, priority=0):
        pos = _math(self.e, 'MakeVector2D', X=self.x(x), Y=self.y(y))
        size = _math(self.e, 'MakeVector2D', X=self.mul(w), Y=self.mul(h))
        self.node('HUD.AddHitBox', Position=pos, Size=size, InName=action,
                  bConsumesInput='true', Priority=priority)

    def button(self, action, label, x, y, w, h=36, accent=False, size=15, priority=5):
        base = AMBER if accent else RAISED
        hover = '(R=1,G=0.68,B=0.31,A=1)' if accent else HOVER
        self.rect(x, y, w, h, self.choose_color(self.hovered(action), hover, base))
        # Label positions are intentional, avoiding font-dependent centering.
        self.text(label, x+(9 if w < 44 else 14), y+(h-size)/2-3, size, INK if accent else WHITE, accent)
        self.hit(action, x, y, w, h, priority)

    def poly(self, points, x, y, color, width=2):
        for (a,b),(c,d) in zip(points, points[1:]):
            self.line(x+a, y+b, x+c, y+d, color, width)

    def circle(self, x, y, r, color, width=2, count=20):
        self.poly([(r*math.cos(i*2*math.pi/count), r*math.sin(i*2*math.pi/count))
                   for i in range(count+1)], x, y, color, width)

    def icon(self, mode, x, y, color):
        if mode == 0:
            self.poly([(5,38),(22,9),(42,0),(39,22),(21,42),(5,38)],x,y,color,2.3)
            self.poly([(9,31),(0,27),(6,15),(18,14)],x,y,color)
            self.poly([(28,32),(28,46),(16,52),(16,40)],x,y,color)
            self.poly([(2,43),(-5,52),(5,48)],x,y,color)
            self.circle(x+30,y+13,4,color,1.7,10)
        elif mode == 1:
            self.poly([(4,28),(11,13),(27,10),(39,21),(36,39),(21,47),(6,41),(4,28)],x,y,color,2.3)
            self.poly([(11,13),(18,25),(9,32),(20,37),(21,47)],x,y,color)
            self.poly([(18,25),(31,24),(36,39)],x,y,color)
            for dx,dy in ((15,-7),(28,-10),(39,0)):
                self.line(x+dx,y+dy+10,x+dx+15,y+dy-6,color,2)
        else:
            self.circle(x+22,y+30,20,color,2.3)
            self.poly([(22,10),(22,0),(33,-8)],x,y,color)
            self.poly([(11,20),(8,30),(12,39)],x,y,color)


def draw_ui(hud, controller_class, fonts):
    from hud_v2 import _number, _metres, _select_string, _concat
    _variables(hud, [('HoveredAction','name','None',False)])
    # Native promotable arithmetic adopts integer pins unless viewport sizes
    # are explicitly converted. Integer division made 1280/1600 become zero.
    layout, entry, result = _function(hud, 'GetUIScale',
        inputs=(('Width','int'),('Height','int')), output='real', pure=True)
    fw=_math(layout,'Conv_IntToDouble',InInt=(entry,'Width'))
    fh=_math(layout,'Conv_IntToDouble',InInt=(entry,'Height'))
    ratio=_math(layout,'FMin',A=_math(layout,'Divide_DoubleDouble',A=fw,B=1600.0),
                B=_math(layout,'Divide_DoubleDouble',A=fh,B=900.0))
    _connect(ratio,result,'Output');_exec(entry,result);_compile(hud)
    e = _graph(hud)
    draw = _event(hud, 'ReceiveDrawHUD')
    size_x=_math(e,'Conv_IntToDouble',InInt=(draw,'SizeX'))
    size_y=_math(e,'Conv_IntToDouble',InInt=(draw,'SizeY'))
    scale_call=e.add_call_function_node('GetUIScale')
    _wire(draw,'SizeX',scale_call,'Width');_wire(draw,'SizeY',scale_call,'Height')
    scale=(scale_call,'Output')
    ox = _math(e,'Multiply_DoubleDouble',A=_math(e,'Subtract_DoubleDouble',A=size_x,B=_math(e,'Multiply_DoubleDouble',A=scale,B=1600)),B=.5)
    oy = _math(e,'Multiply_DoubleDouble',A=_math(e,'Subtract_DoubleDouble',A=size_y,B=_math(e,'Multiply_DoubleDouble',A=scale,B=900)),B=.5)
    find = _call(e, 'GameplayStatics.GetActorOfClass')
    _value(find,'ActorClass',controller_class.get_path_name())
    _exec(draw,find)
    valid = _call(e,'KismetSystemLibrary.IsValid')
    _wire(find,'ReturnValue',valid,'Object')
    gate = e.add_branch_node()
    _wire(valid,'ReturnValue',gate,'Condition'); _exec(find,gate)
    variables={}
    for name in ('WeaponMode','YawDegrees','PitchDegrees','LaunchHeight','MeteorTargetX','MeteorTargetY','MeteorAngle','SlowMoActive'):
        n=e.add_get_member_variable_node(name,controller_class.get_path_name())
        _wire(find,'ReturnValue',n,'self'); variables[name]=(n,name)
    meteor=_math(e,'EqualEqual_IntInt',A=variables['WeaponMode'],B=1)
    embed=_math(e,'GreaterEqual_DoubleDouble',A=variables['MeteorAngle'],B=78)
    simple=e.add_branch_node(); _connect(_get(e,'MinimalUI'),simple,'Condition'); _exec(gate,simple)
    # Separate draw chains allow the world to remain fully visible in minimal mode.
    mini_entry=_call(e,'HUD.DrawRect'); _value(mini_entry,'ScreenW',0); _value(mini_entry,'ScreenH',0); _exec(simple,mini_entry)
    mini=Painter(e,mini_entry,scale,ox,oy,fonts)
    mini.button('minimal','显示界面  [Tab]',1398,28,170,40)
    full_entry=_call(e,'HUD.DrawRect'); _value(full_entry,'ScreenW',0); _value(full_entry,'ScreenH',0); _exec(simple,full_entry,'else')
    p=Painter(e,full_entry,scale,ox,oy,fonts)

    # Brand, thin orange structural mark, restrained top toolbar.
    p.rect(32,28,390,76,INK); p.rect(32,28,4,76,AMBER)
    p.text('崩塌实验室',52,34,30,WHITE,True)
    p.text('自由破坏  /  城市试验场 01',54,78,13,MUTED)
    p.button('help','操作指南  [H]',1310,28,142,40)
    p.button('minimal','隐藏  [Tab]',1464,28,104,40)

    # Camera controls occupy a compact top-right strip.
    p.rect(1280,108,288,144)
    p.text('观察视角',1298,120,18,WHITE,True)
    p.text('按住右键拖动 · 360° 环绕',1298,151,14,MUTED)
    p.button('camera_left','左转',1298,181,76,36)
    p.button('camera_reset','归位 [F]',1382,181,84,36)
    p.button('camera_right','右转',1474,181,76,36)
    p.text('滚轮调远近    Q / E 调高低',1298,226,12,MUTED)

    # Three live parameter rows, expressed in metres and degrees.
    p.rect(1280,422,288,292)
    p.rect(1280,422,288,3,AMBER)
    p.text('瞄准设置',1298,438,20,WHITE,True)
    mode_text=_select_string(e,meteor[0],'陨石落点与入射角','发射方向与高度')
    p.text(mode_text,1298,470,13,MUTED)
    primary_label=_select_string(e,meteor[0],'横向落点','水平方向')
    secondary_label=_select_string(e,meteor[0],'纵向落点','发射高度')
    angle_label=_select_string(e,meteor[0],'入射角度','发射仰角')
    primary_value=_select_string(e,meteor[0],_concat(e,_metres(e,*variables['MeteorTargetX']),' 米'),_concat(e,_number(e,*variables['YawDegrees']),'°'))
    secondary_value=_select_string(e,meteor[0],_concat(e,_metres(e,*variables['MeteorTargetY']),' 米'),_concat(e,_metres(e,*variables['LaunchHeight']),' 米'))
    angle_value=_select_string(e,meteor[0],_concat(e,_number(e,*variables['MeteorAngle']),'°'),_concat(e,_number(e,*variables['PitchDegrees']),'°'))
    for row,(action,label,value,key) in enumerate((('primary',primary_label,primary_value,'A / D'),('secondary',secondary_label,secondary_value,'W / S'),('angle',angle_label,angle_value,'↑ / ↓'))):
        yy=503+row*66
        p.line(1298,yy-8,1550,yy-8)
        p.text(label,1298,yy,14,MUTED)
        p.text(key,1298,yy+24,11,MUTED)
        p.text(value,1390,yy+11,20,WHITE,True)
        p.button(action+'_minus','−',1480,yy+4,32,36,size=20)
        p.button(action+'_plus','+',1518,yy+4,32,36,size=20)

    # Bottom weapon cards, large original line-art silhouettes, selected accent.
    p.text('选择冲击物',32,694,18,WHITE,True)
    for mode,(action,name,caption) in enumerate((('weapon_rocket','火箭','高速贯穿'),('weapon_meteor','陨石','天降冲击'),('weapon_ball','铁球','重力撞击'))):
        xx=32+mode*178; yy=726; ww=166
        selected=_math(e,'EqualEqual_IntInt',A=variables['WeaponMode'],B=mode)
        color=p.choose_color(selected,AMBER,WHITE)
        background=p.choose_color(selected,AMBER_BG,p.choose_color(p.hovered(action),HOVER,PANEL))
        p.rect(xx,yy,ww,128,background)
        p.rect(xx,yy,ww,3,p.choose_color(selected,AMBER,LINE))
        p.text(str(mode+1),xx+13,yy+12,13,color,True)
        p.icon(mode,xx+100,yy+27,color)
        p.text(name,xx+14,yy+59,25,color,True)
        p.text(caption,xx+14,yy+99,13,MUTED)
        p.hit(action,xx,yy,ww,128,5)

    rocket=_math(e,'EqualEqual_IntInt',A=variables['WeaponMode'],B=0)
    meteor_title=_select_string(e,embed[0],'陨石 · 嵌入冲击','陨石 · 贯穿冲击')
    current_title=_select_string(e,rocket[0],'火箭 · 高速贯穿',_select_string(e,meteor[0],meteor_title,'铁球 · 重力撞击'))
    meteor_tip=_select_string(e,embed[0],'陨石嵌入楼体，近距离释放冲击。','斜向穿过楼体，观察入口与出口的破坏。')
    current_tip=_select_string(e,rocket[0],'瞄准薄弱位置，击穿墙体并引发连续坍塌。',_select_string(e,meteor[0],meteor_tip,'调整高度与角度，让重力放大撞击。'))
    p.rect(590,748,650,106,INK)
    p.rect(590,748,3,106,AMBER)
    p.text(current_title,612,762,23,WHITE,True)
    p.text(current_tip,612,801,15,MUTED)
    p.text('改变角度，看看这次会发生什么。',612,827,12,MUTED)
    p.button('fire','发射冲击    [空格]',1280,738,288,62,True,23)
    p.button('rebuild','重建场景    [R]',1280,812,288,42,size=16)

    p.text('右键拖动观察  ·  Z / C 快捷绕楼  ·  松开右键后调整瞄准与发射',32,872,13,WHITE)
    p.text('H 操作指南  ·  Tab 隐藏界面',1298,872,12,MUTED)

    help_branch=e.add_branch_node(); _connect(_get(e,'HelpOpen'),help_branch,'Condition'); _exec(p.tail,help_branch)
    overlay=_call(e,'HUD.DrawRect'); _value(overlay,'ScreenW',0);_value(overlay,'ScreenH',0);_exec(help_branch,overlay)
    h=Painter(e,overlay,scale,ox,oy,fonts)
    h.rect(0,0,1600,900,'(R=0.005,G=0.008,B=0.011,A=0.72)')
    h.hit('modal_block',0,0,1600,900,50)
    h.rect(410,132,780,634,INK);h.rect(410,132,780,4,AMBER)
    h.text('操作指南',446,158,30,WHITE,True)
    h.text('选好位置，发射，然后欣赏连锁坍塌。',446,206,16,MUTED)
    for title,yy,lines in (
        ('01  选择与发射',252,('点击武器卡，或按 1 / 2 / 3 切换火箭、陨石、铁球。','点击橙色发射按钮，或按空格发射；R 重建完整建筑。')),
        ('02  调整瞄准',374,('A / D 调整方向或横向落点；W / S 调整高度或纵向落点。','↑ / ↓ 调整角度；也可以点击右侧的 − / + 按钮。','陨石入射角小于 78° 时贯穿，达到 78° 时嵌入。')),
        ('03  自由观察',523,('按住鼠标右键拖动，围绕建筑旋转；滚轮拉近、拉远。','Z / C 每次转动 45°；Q / E 调整高低；F 恢复默认镜头。','按住右键时，A / D 绕楼、W / S 调距离，Shift 加速。'))):
        h.line(446,yy-9,1154,yy-9)
        h.text(title,446,yy,19,AMBER,True)
        for i,line in enumerate(lines):h.text(line,446,yy+34+i*27,16,WHITE)
    h.button('help_close','明白了，继续试验  [H]',846,696,308,42,True,17,priority=100)

    clicked=_event(hud,'ReceiveHitBoxClick',(0,1300))
    action=e.add_call_function_node('UIAction');_wire(clicked,'BoxName',action,'Action');_exec(clicked,action)
    over=_event(hud,'ReceiveHitBoxBeginCursorOver',(0,1500))
    over_set=e.add_set_member_variable_node('HoveredAction');_wire(over,'BoxName',over_set,'HoveredAction');_exec(over,over_set)
    out=_event(hud,'ReceiveHitBoxEndCursorOver',(0,1700))
    same=_math(e,'EqualEqual_NameName',A=_get(e,'HoveredAction'),B=(out,'BoxName'))
    clear=e.add_branch_node();_connect(same,clear,'Condition');_exec(out,clear)
    out_set=e.add_set_member_variable_node('HoveredAction');_value(out_set,'HoveredAction','None');_exec(clear,out_set)
    _compile(hud)
