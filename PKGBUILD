# Local build: the code lives in this very directory, nothing is downloaded.
# To rebuild after a change:  makepkg -fi

pkgname=rinth
pkgver=0.1.0
pkgrel=1
pkgdesc='CLI package manager for Modrinth projects: plugins, mods, shaders and resource packs'
arch=('any')
url='https://github.com/drygo/rinth'
license=('MIT')
depends=('python>=3.11')
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
options=('!debug')
source=()

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation
}

check() {
  cd "$startdir"
  python -m unittest discover -s tests -t .
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" dist/rinth-${pkgver}-*.whl
  install -Dm644 completions/rinth.fish \
    "$pkgdir/usr/share/fish/vendor_completions.d/rinth.fish"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
