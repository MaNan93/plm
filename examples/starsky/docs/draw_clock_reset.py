"""Fully wired original PIPE schematics; explicit junctions, no named-net gaps."""
from pathlib import Path
from html import escape
from PyQt5.QtWidgets import QApplication
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtGui import QImage, QPainter

OUT=Path(__file__).resolve().parent
W,H=2460,2110
INK='#28465d'; CTRL='#8055ab'; CLK='#087f9c'; RST='#b45d28'
class Sheet:
    def __init__(self,title):
        self.junctions=[]
        self.parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
        self.text(35,45,title,28)
        self.text(35,80,'original PIPE · PHY-output PCLK · no DPR · SEL_SYNC=0',18)
        self.text(35,110,'● 表示分支连接；交叉处无圆点则不连接。所有信号均连续接线；每页重复绘制同一个 mode 译码器。',17)
    def text(self,x,y,s,size=17,color=INK):
        self.parts.append(f'<text x="{x}" y="{y}" font-family="Microsoft YaHei" font-size="{size}" fill="{color}">{escape(s)}</text>')
    def wire(self,*pts,color=INK):
        points=' '.join(f'{x},{y}' for x,y in pts)
        # White underlay makes unjoined crossings unambiguous.
        self.parts.append(f'<polyline points="{points}" fill="none" stroke="white" stroke-width="7"/>')
        self.parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.2"/>')
    def dot(self,p,color=INK):
        self.junctions.append((p,color))
    def gate(self,kind,x,y,n=2):
        if kind=='AND': d=f'M{x},{y-32} H{x+30} A32,32 0 0 1 {x+30},{y+32} H{x} Z'; end=x+62
        else: d=f'M{x},{y-32} Q{x+40},{y-32} {x+75},{y} Q{x+40},{y+32} {x},{y+32} Q{x+22},{y} {x},{y-32} Z';end=x+75
        self.parts.append(f'<path d="{d}" fill="#f0f5fa" stroke="{INK}" stroke-width="2"/>')
        ins=[(x if kind=='AND' else x+8,y+(i-(n-1)/2)*22) for i in range(n)]
        return ins,(end,y)
    def mux(self,x,y,name):
        self.parts.append(f'<path d="M{x},{y-42} L{x+74},{y-28} L{x+74},{y+28} L{x},{y+42} Z" fill="#e8f5fb" stroke="{CLK}" stroke-width="2"/>')
        self.text(x+8,y-15,'1',14);self.text(x+8,y+28,'0',14)
        self.text(x-5,y-58,name,16,CLK)
        return (x,y-22),(x,y+22),(x+37,y+35),(x+74,y)
    def railtap(self,x,y,dst,color=CTRL,bend=None):
        self.wire((x,y),(bend if bend is not None else dst[0]-30,y),(bend if bend is not None else dst[0]-30,dst[1]),dst,color=color)
        self.dot((x,y),color)
    def decoder(self):
        # Input rails and their inverters, followed by three equality decoders.
        self.text(35,160,'mode[1]',16);self.text(125,160,'mode[0]',16)
        for x in (60,150): self.wire((x,175),(x,650),color=CTRL)
        for src,y,nx,label in [(60,230,240,'~m1'),(150,290,280,'~m0')]:
            self.railtap(src,y,(180,y),CTRL,165)
            self.parts.append(f'<path d="M180,{y-17} L180,{y+17} L208,{y} Z" fill="#f2eafa" stroke="{CTRL}" stroke-width="2"/><circle cx="214" cy="{y}" r="6" fill="white" stroke="{CTRL}" stroke-width="2"/>')
            self.wire((220,y),(nx,y),(nx,650),color=CTRL);self.text(nx+7,y+19,label,14,CTRL)
        dr={}
        for k,y,rails in [(0,380,(240,280)),(1,480,(240,150)),(2,580,(60,280))]:
            ins,o=self.gate('AND',350,y)
            for j,(x,p) in enumerate(zip(rails,ins)):self.railtap(x,p[1],p,CTRL)
            rx=445+k*23
            self.wire(o,(rx,y),(rx,710+k*25),color=CTRL)
            self.text(365,y-45,f'D{k}',16,CTRL);dr[k]=(rx,y)
        # Shared select nets: g1b0=g2b0=D0|D1; g1b1=D2; g2b1=D1|D2.
        origin={}
        for tag,y,ks,bus in [('S10 / S20',700,(0,1),700),('S21',820,(1,2),760)]:
            ins,o=self.gate('OR',550,y)
            for j,(k,p) in enumerate(zip(ks,ins)):
                x,dy=dr[k];ty=660+j*22 if y==700 else 760+j*22
                # Extend the D rail to the branch height before tapping.
                self.wire((x,dy),(x,ty),color=CTRL)
                self.railtap(x,ty,p,CTRL,510+j*15)
            self.wire(o,(bus,y),(bus,2060),color=CTRL);self.text(bus-100,y-16,tag,16,CTRL)
            origin[bus]=y
        for k,bus,y,label in [(2,730,910,'S11'),(0,790,940,'S30'),(1,820,970,'S31'),(2,850,1000,'S32')]:
            x,dy=dr[k]
            self.wire((x,dy),(x,y),(bus,y),(bus,2060),color=CTRL)
            self.dot((x,dy),CTRL);self.text(bus-55,y-12,label,16,CTRL);origin[bus]=y
        self.text(35,1090,'译码为 00 / 01 / 10；mode=11 时选择全零。',16)
        return { '10':700,'20':700,'11':730,'21':760,'30':790,'31':820,'32':850 }
    def controls(self,rails,key,dst,y,bend=None):
        x=rails[key];self.railtap(x,y,dst,CTRL,bend)
    def pairout(self,src,g,name,color):
        x=2180;y=src[1]
        self.wire(src,(x,y),color=color)
        for lane,dy in [(g*2,-17),(g*2+1,17)]:
            self.wire((x,y),(x,y+dy),(2240,y+dy),color=color)
            self.text(2250,y+dy+6,f'{name}[{lane}]',16,color)
        self.dot((x,y),color)
    def save(self,name):
        for p,color in self.junctions:self.parts.append(f'<circle cx="{p[0]}" cy="{p[1]}" r="4" fill="{color}"/>')
        self.parts.append('</svg>');path=OUT/(name+'.svg');path.write_text('\n'.join(self.parts),encoding='utf-8')
        renderer=QSvgRenderer(str(path));assert renderer.isValid()
        img=QImage(W,H,QImage.Format_ARGB32);img.fill(0xffffffff)
        painter=QPainter(img);renderer.render(painter);painter.end();assert img.save(str(OUT/(name+'.png')))

app=QApplication.instance() or QApplication([])
s=Sheet('Starsky · 完整连线时钟电路');r=s.decoder()
# Four continuous source rails; controller pclk taps are visible on these rails.
cx={0:1160,1:1210,2:1260,3:1310}
for c,lane in enumerate((0,2,4,6)):
    y=190+c*60;x=cx[c]
    s.text(920,y-12,f'phy_pclk_out[{lane}]',17,CLK)
    s.wire((920,y),(x,y),(x,2030),color=CLK)
    s.wire((x,y),(2200,y),color=CLK);s.dot((x,y),CLK);s.text(2210,y+6,f'pcie{c}_pclk',17,CLK)
s.text(920,460,'phy_pclk_out[1/3/5/7] 不参与工作 / 反馈时钟选源。',16)
s.text(1500,600,'两候选：仅 1 个 MUX；无选择同步器或时钟 AND',20)
s.railtap(cx[0],780,(2080,780),CLK);s.pairout((2080,780),0,'phy_pclk_in',CLK)
for g,y,other in [(1,1110,1),(2,1430,2)]:
    a,b,sel,o=s.mux(1650,y,f'G{g} MUX')
    s.railtap(cx[0],y-70,a,CLK,1550);s.railtap(cx[other],y+60,b,CLK,1580)
    s.controls(r,f'{g}0',sel,y+115,1610)
    s.pairout(o,g,'phy_pclk_in',CLK)
s.text(1460,1670,'三候选：补 tie0，完整两级平衡 MUX 树',20)
a,b,sel,left=s.mux(1600,1770,'G3 MUX0')
s.railtap(cx[0],1710,a,CLK,1520);s.railtap(cx[2],1830,b,CLK,1545)
s.controls(r,'30',sel,1870,1560)
a,b,sel,right=s.mux(1600,1960,'G3 MUX1')
s.railtap(cx[3],1910,a,CLK,1510)
s.text(1460,2020,'tie0',16,CLK);s.wire((1500,2010),(1550,2010),(1550,b[1]),b,color=CLK)
s.controls(r,'32',sel,2050,1570)
a,b,sel,o=s.mux(1990,1855,'G3 MUX2')
s.wire(left,(1850,left[1]),(1850,a[1]),a,color=CLK)
s.wire(right,(1880,right[1]),(1880,b[1]),b,color=CLK)
vi,vo=s.gate('OR',1800,2040)
for j,k in enumerate(('30','31')):s.controls(r,k,vi[j],2010+j*45,1740+j*20)
s.wire(vo,(1940,vo[1]),(1940,sel[1]+25),(sel[0],sel[1]+25),sel,color=CTRL)
s.pairout(o,3,'phy_pclk_in',CLK)
s.text(35,2078,'使用前提：合法 mode 在上电前配置并保持静态；不要求全零过渡态。图中为逻辑平衡，物理延迟由实现流程控制。',17)
s.save('clock_connected')

s=Sheet('Starsky · 完整连线复位电路');r=s.decoder()
for c,x in cx.items():
    y=190+c*60
    s.text(940,y-12,f'pcie{c}_rst_n',18,RST)
    s.wire((940,y),(x,y),(x,1950),color=RST)
s.text(1460,600,'低有效输入直接参与 AND / OR；无复位同步器或反相器。',18)
s.railtap(cx[0],780,(2090,780),RST);s.pairout((2090,780),0,'phy_rst_n',RST)
for g,y,cands in [(1,1130,[0,1]),(2,1490,[0,2]),(3,1860,[0,2,3])]:
    terms=[]
    for bit,c in enumerate(cands):
        yy=y+(bit-(len(cands)-1)/2)*110
        ins,o=s.gate('AND',1640,yy)
        s.railtap(cx[c],yy-48,ins[0],RST,1520+bit*25)
        s.controls(r,f'{g}{bit}',ins[1],yy+45,1580+bit*15)
        terms.append(o)
    ins,o=s.gate('OR',2020,y,len(cands))
    for j,(src,dst) in enumerate(zip(terms,ins)):
        bend=1840+j*30;s.wire(src,(bend,src[1]),(bend,dst[1]),dst,color=RST)
    s.pairout(o,g,'phy_rst_n',RST)
s.text(35,2078,'mode=11 时 G1–G3 复位输出为 0；G0 始终直连 pcie0_rst_n。顶层负责复位产生和同步释放。',17)
s.save('reset_connected')
# Preserve the original delivery filenames as a two-page combined schematic.
clock=(OUT/'clock_connected.svg').read_text(encoding='utf-8')
reset=(OUT/'reset_connected.svg').read_text(encoding='utf-8')
combined=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H*2}" viewBox="0 0 {W} {H*2}">' + clock.replace('<svg ', '<svg y="0" ',1) + reset.replace('<svg ',f'<svg y="{H}" ',1) + '</svg>'
path=OUT/'clock_reset.svg';path.write_text(combined,encoding='utf-8')
renderer=QSvgRenderer(str(path));assert renderer.isValid()
img=QImage(W,H*2,QImage.Format_ARGB32);img.fill(0xffffffff)
painter=QPainter(img);renderer.render(painter);painter.end();assert img.save(str(OUT/'clock_reset.png'))
print('Rendered two fully connected SVG/PNG sheets')
