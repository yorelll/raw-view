"""Help HTML content for format explanation."""

HELP_HTML = """
<h2>RAW/YUV Format Help</h2>
<h3>RAW aligned (LSB/MSB)</h3>
<ul>
<li>LSB aligned: valid n-bit data in low bits (e.g. RAW10 uses bit[9:0]).</li>
<li>MSB aligned: valid n-bit data in high bits (e.g. RAW10 uses bit[15:6]).</li>
</ul>
<h3>RAW8 Example (W=4,H=1)</h3>
<p>P0=0x0A, P1=0x14, P2=0x1E, P3=0x28 &rarr; bytes: <code>0A 14 1E 28</code></p>
<h3>RAW10 (16-bit aligned) Example</h3>
<ul>
<li>P0=0x001, P1=0x155</li>
<li>LSB: P0=0x0001 (&quot;01 00&quot;), P1=0x0155 (&quot;55 01&quot;)</li>
<li>MSB: P0=0x0040 (&quot;40 00&quot;), P1=0x5540 (&quot;40 55&quot; little-endian)</li>
</ul>
<h3>RAW12 Packed (2 pixels -&gt; 3 bytes)</h3>
<p>B0=P0[7:0], B1=(P0[11:8])|(P1[3:0]&lt;&lt;4), B2=P1[11:4]</p>
<h3>RAW10 Packed (MIPI)</h3>
<p>B0=P0[7:0], B1=P1[7:0], B2=P2[7:0], B3=P3[7:0], B4=P0[9:8]|(P1[9:8]&lt;&lt;2)|(P2[9:8]&lt;&lt;4)|(P3[9:8]&lt;&lt;6)</p>
<h3>YUV420 I420 (W=4,H=2)</h3>
<p>Layout: Y(8 bytes) + U(2 bytes) + V(2 bytes). Each U/V sample covers one 2x2 luma block.</p>
<h3>YUV Subformats</h3>
<ul>
<li>420: I420, YV12, NV12, NV21</li>
<li>422: YUYV, UYVY, NV16</li>
</ul>
<h3>Image Conversion Rules</h3>
<p>PNG/JPEG/BMP are loaded as BGR and optionally resized.</p>
<ul>
<li>RAW conversion supports Bayer (default, RGGB) or gray source.</li>
<li>RAW viewing supports Bayer color preview (RGGB) or grayscale preview.</li>
<li>YUV conversion uses RGB-to-YUV conversion with subformat-specific chroma sampling.</li>
</ul>
"""
