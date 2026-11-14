//! One icon content and DIP frame rule for every app and monitor.
pub struct Canvas { pub edge: u32, pub rgba: Vec<u8> }

pub fn frame_css(taskbar_height: i32, scale: f64) -> f64 {
    let scale = if scale.is_finite() && scale > 0.0 {scale} else {1.0};
    let height = (f64::from(taskbar_height)/scale).max(0.0);
    const CONTROL_INSET: f64 = 8.0;
    const SMALL_ICON: f64 = 16.0;
    const STANDARD_ICON: f64 = 24.0;
    (height-2.0*CONTROL_INSET).clamp(SMALL_ICON,STANDARD_ICON).min(height)
}

/// Remove transparent canvas padding, preserve aspect ratio, and center visible
/// content in a square. Relative alpha ignores negligible fringes, while icons
/// whose entire artwork is translucent remain representable.
pub fn normalize(width: u32, height: u32, rgba: &[u8]) -> Option<Canvas> {
    let count = (width as usize).checked_mul(height as usize)?;
    if width == 0 || height == 0 || count > 4_194_304 || rgba.len() != count.checked_mul(4)? {return None;}
    let max_alpha = rgba.chunks_exact(4).map(|p|p[3]).max()?;
    if max_alpha == 0 {return None;}
    let threshold = (max_alpha/16).max(1);
    let (mut left,mut top,mut right,mut bottom) = (width,height,0,0);
    for (index,pixel) in rgba.chunks_exact(4).enumerate() {
        if pixel[3] < threshold {continue;}
        let x = index as u32 % width; let y = index as u32 / width;
        left=left.min(x);top=top.min(y);right=right.max(x+1);bottom=bottom.max(y+1);
    }
    let (w,h) = (right-left,bottom-top);
    let edge=w.max(h);
    let (dx,dy)=((edge-w)/2,(edge-h)/2);
    let mut out=vec![0;edge as usize*edge as usize*4];
    for y in 0..h {
        let source=((top+y) as usize*width as usize+left as usize)*4;
        let target=((dy+y) as usize*edge as usize+dx as usize)*4;
        out[target..target+w as usize*4].copy_from_slice(&rgba[source..source+w as usize*4]);
    }
    Some(Canvas {edge,rgba:out})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn padding_is_removed_without_changing_artwork_aspect_ratio() {
        let mut pixels=vec![0;64*64*4];
        for y in 20..44 {for x in 16..48 {pixels[(y*64+x)*4..(y*64+x)*4+4].copy_from_slice(&[100,150,200,255]);}}
        let canvas=normalize(64,64,&pixels).unwrap();
        assert_eq!(canvas.edge,32);
        assert_eq!(canvas.rgba.chunks_exact(4).filter(|p|p[3]>0).count(),32*24);
        assert!(canvas.rgba[..32*4*4].iter().all(|v|*v==0));
    }
    #[test]
    fn empty_invalid_and_oversized_sources_are_rejected() {
        assert!(normalize(2,2,&[0;16]).is_none());
        assert!(normalize(0,2,&[]).is_none());
        assert!(normalize(2,2,&[0;12]).is_none());
        assert!(normalize(u32::MAX,u32::MAX,&[]).is_none());
    }
    #[test]
    fn translucent_art_is_preserved_and_negligible_noise_does_not_define_size() {
        let mut pixels=vec![0;16*16*4];pixels[3]=1;
        for y in 4..12 {for x in 4..12 {pixels[(y*16+x)*4+3]=64;}}
        let canvas=normalize(16,16,&pixels).unwrap();
        assert_eq!(canvas.edge,8);
        assert!(canvas.rgba.chunks_exact(4).all(|p|p[3]==64));
    }
    #[test]
    fn logical_frame_is_consistent_across_dpi_and_bounded_by_the_taskbar() {
        for scale in [1.0,1.25,1.5,1.75,2.0,3.0,4.0] {
            for logical in [24.0,28.0,32.0,40.0,48.0,64.0] {
                let physical=(logical*scale) as i32;
                let frame=frame_css(physical,scale);
                assert!(frame<=f64::from(physical)/scale && frame<=24.0);
                if logical>=40.0 {assert_eq!(frame,24.0);}
                if logical==32.0 {assert_eq!(frame,16.0);}
            }
        }
    }
}
