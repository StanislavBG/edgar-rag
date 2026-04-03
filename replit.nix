{ pkgs }: {
  deps = [
    pkgs.python311Packages.pip
    pkgs.python311
  ];
  env = {
    LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
      pkgs.stdenv.cc.cc.lib
    ];
  };
}
