#!/bin/bash
set -e

ISO=/tmp/ubuntu.iso
BOOT_DIR=/boot/ubuntu-installer
ISO_MOUNT=/mnt/ubuntu_iso
CIDATA_MOUNT=/mnt/cidata
SDA1_UUID=f267994a-c43c-4e23-a326-a283a49273e0

echo '=============================='
echo ' NEXORA Ubuntu 24.04 Deploy'
echo '=============================='
echo

# STEP 1: Verificar ISO
echo '[1/7] Verificando ISO...'
SIZE=$(stat -c%s "$ISO" 2>/dev/null || echo 0)
echo "Tamanio: $SIZE bytes"
if [ "$SIZE" -lt 3200000000 ]; then
    echo 'ERROR: ISO incompleto. Espera a que termine la descarga.'
    exit 1
fi
echo 'ISO OK'

# STEP 2: dd ISO -> /dev/sdb
echo
echo '[2/7] Escribiendo ISO a /dev/sdb (3-4 min)...'
dd if=$ISO of=/dev/sdb bs=4M oflag=direct 2>&1 | tail -3
sync
sleep 3
partprobe /dev/sdb 2>/dev/null || true
sleep 2
echo 'dd DONE. Particiones en sdb:'
lsblk /dev/sdb -o NAME,SIZE,TYPE,FSTYPE

# STEP 3: Extraer kernel e initrd
echo
echo '[3/7] Extrayendo vmlinuz e initrd del ISO...'
mkdir -p $BOOT_DIR $ISO_MOUNT
mount -o loop,ro $ISO $ISO_MOUNT
ls $ISO_MOUNT/casper/ | head -10
cp $ISO_MOUNT/casper/vmlinuz $BOOT_DIR/
cp $ISO_MOUNT/casper/initrd $BOOT_DIR/
umount $ISO_MOUNT
echo "vmlinuz e initrd copiados en $BOOT_DIR"

# STEP 4: Crear particion CIDATA en /dev/sdb
echo
echo '[4/7] Creando particion CIDATA en /dev/sdb...'
sgdisk --new=0:0:0 --typecode=0:0700 --change-name=0:CIDATA /dev/sdb 2>&1 | tail -3
partprobe /dev/sdb
sleep 2

CIDATA_DEV=$(lsblk /dev/sdb -o NAME,PARTLABEL -n 2>/dev/null | grep -i CIDATA | awk '{print "/dev/"$1}' | head -1)
if [ -z "$CIDATA_DEV" ]; then
    # Tomar la ultima particion de sdb
    CIDATA_DEV=$(lsblk /dev/sdb -o NAME -n 2>/dev/null | tail -1 | awk '{print "/dev/"$1}')
fi
echo "CIDATA device: $CIDATA_DEV"
mkfs.vfat -n CIDATA "$CIDATA_DEV"

mkdir -p $CIDATA_MOUNT
mount "$CIDATA_DEV" $CIDATA_MOUNT
cp /tmp/user-data $CIDATA_MOUNT/
cp /tmp/meta-data $CIDATA_MOUNT/
ls -la $CIDATA_MOUNT/
umount $CIDATA_MOUNT
echo 'CIDATA listo'

# STEP 5: Agregar entrada GRUB en sda
echo
echo '[5/7] Configurando GRUB entry en /dev/sda...'

cat >> /etc/grub.d/40_custom << 'GRUBEOF'
menuentry "Ubuntu 24.04 Autoinstall" {
  insmod all_video
  insmod ext2
  insmod gzio
  search --no-floppy --set=root --fs-uuid f267994a-c43c-4e23-a326-a283a49273e0
  echo "Cargando Ubuntu 24.04 installer..."
  linux /boot/ubuntu-installer/vmlinuz autoinstall ds=nocloud quiet splash
  initrd /boot/ubuntu-installer/initrd
}
GRUBEOF

chmod +x /etc/grub.d/40_custom
update-grub 2>&1 | tail -5
echo 'GRUB actualizado'

# STEP 6: grub-reboot al installer
echo
echo '[6/7] Configurando next-boot...'
grub-reboot "Ubuntu 24.04 Autoinstall" 2>&1
echo 'Next boot configurado: Ubuntu 24.04 Autoinstall'

# STEP 7: Resumen
echo
echo '[7/7] TODO LISTO'
echo '==============================='
echo 'La instalacion tomara ~30-40 min'
echo 'El servidor reiniciara automaticamente'
echo 'Reconectar por SSH a 45.184.225.4'
echo '==============================='
echo 'Cuando estes listo: sudo reboot'
